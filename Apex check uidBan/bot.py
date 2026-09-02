import asyncio
import html
import json
import logging
import time
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ============================================================
#                    🤖 APEX DATA BOT CONFIG
# ============================================================

BOT_TOKEN = "8638501669:AAHUgxWcM5RRgiaDWw6w0pz5bvpUPH4DdOo"

PLAYER_API = "https://player-info-ob54.vercel.app/player-info"
BAN_API = "https://nirob-ban-check.vercel.app/bancheck"
BAN_KEY = "nirob"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=6, connect=3, sock_connect=3, sock_read=4)
CACHE_TTL = 15
HISTORY_LIMIT = 10
MAX_PROVIDER_ATTEMPTS = 2
RETRY_DELAY = 0.15

BASE_DIR = Path(__file__).resolve().parent
REQUIRED_CHANNELS_FILE = BASE_DIR / "channels.txt"
DATA_DIR = BASE_DIR / "data"
USERS_FILE = DATA_DIR / "users.json"
HISTORY_FILE = DATA_DIR / "history.json"
STATS_FILE = DATA_DIR / "stats.json"

router = Router()

CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
INFLIGHT: dict[str, asyncio.Task[dict[str, Any]]] = {}
HTTP_SESSION: aiohttp.ClientSession | None = {}

def ensure_files():
    DATA_DIR.mkdir(exist_ok=True)
    if not USERS_FILE.exists():
        USERS_FILE.write_text("{}", encoding="utf-8")
    if not HISTORY_FILE.exists():
        HISTORY_FILE.write_text("{}", encoding="utf-8")
    if not STATS_FILE.exists():
        STATS_FILE.write_text(json.dumps({"total_searches": 0, "private_searches": 0, "group_searches": 0, "known_users": [], "known_groups": []}, indent=2), encoding="utf-8")
    if not REQUIRED_CHANNELS_FILE.exists():
        REQUIRED_CHANNELS_FILE.write_text("https://t.me/ApexXChannel\n", encoding="utf-8")

def load_json(path: Path, default: Any):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def save_json(path: Path, data: Any):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)

def normalize_chat_reference(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.startswith("@"):
        return value
    if value.startswith(("https://", "http://")):
        parsed = urlparse(value)
        host = parsed.netloc.lower()
        path = parsed.path.strip("/")
        if host in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
            if path and not path.startswith("+") and not path.startswith("joinchat/"):
                username = path.split("/", 1)[0]
                if username:
                    return "@" + username
    if value.lstrip("-").isdigit():
        return value
    return ""

def load_required_channels() -> list[dict[str, str]]:
    channels = []
    seen = set()
    if not REQUIRED_CHANNELS_FILE.exists():
        return channels
    for raw_line in REQUIRED_CHANNELS_FILE.read_text(encoding="utf-8").splitlines():
        link = raw_line.strip()
        if not link or link.startswith("#"):
            continue
        chat_ref = normalize_chat_reference(link)
        if not chat_ref:
            continue
        key = chat_ref.lower()
        if key in seen:
            continue
        seen.add(key)
        channels.append({"chat_id": chat_ref, "link": link})
    return channels

def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔎 CHECK UID", callback_data="new_search"),
                InlineKeyboardButton(text="📊 BOT STATUS", callback_data="status"),
            ],
            [
                InlineKeyboardButton(text="🛡️ BAN STATUS", callback_data="ban_status"),
                InlineKeyboardButton(text="👤 CONTACT OWNER", url="https://t.me/ApexXForge"),
            ],
        ]
    )

def result_keyboard(uid: str) -> InlineKeyboardMarkup:
    return home_keyboard()

def verify_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for index, item in enumerate(load_required_channels(), start=1):
        buttons.append([InlineKeyboardButton(text="📢 JOIN OFFICIAL CHANNEL" if index == 1 else f"📢 JOIN CHANNEL {index}", url=item["link"])])
    buttons.append([InlineKeyboardButton(text="⚡ VERIFY & UNLOCK ACCESS", callback_data="verify_join")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def safe_value(value: Any, default: str = "N/A") -> str:
    if value is None or value == "":
        return default
    return html.escape(str(value))

def valid_uid(uid: str) -> bool:
    return uid.isdigit() and 5 <= len(uid) <= 15

def is_private(message: Message) -> bool:
    return message.chat.type == ChatType.PRIVATE

def is_group(message: Message) -> bool:
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

async def check_membership(bot: Bot, user_id: int) -> tuple[bool, list[str]]:
    required = load_required_channels()
    if not required:
        return True, []
    missing = []
    for item in required:
        ref = item["chat_id"]
        is_member = False
        try:
            member = await bot.get_chat_member(chat_id=ref, user_id=user_id)
            status = str(getattr(member.status, "value", member.status)).lower()
            if status in {"creator", "owner", "administrator", "member"}:
                is_member = True
            elif status == "restricted" and getattr(member, "is_member", True):
                is_member = True
        except Exception:
            pass
        if not is_member:
            missing.append(item["link"])
    return len(missing) == 0, missing

async def require_private_access(message: Message, bot: Bot) -> bool:
    if not is_private(message):
        return True
    verified, _ = await check_membership(bot, message.from_user.id)
    if verified:
        users = load_json(USERS_FILE, {})
        users[str(message.from_user.id)] = {"verified": True, "username": message.from_user.username, "name": message.from_user.full_name, "updated_at": int(time.time())}
        save_json(USERS_FILE, users)
        return True
    await message.answer("⚡ <b>APEX FF PLAYER INFO BOT</b>\n━━━━━━━━━━━━━━━━━━\n\n🔐 <b>Verification Required</b>\n\nWelcome! To continue, please complete the quick verification below.\n\n① Join our official channel\n② Return here and tap Verify\n③ Get instant access", reply_markup=verify_keyboard())
    return False

def normalize_uid(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text

def get_nested(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current

def response_uid_candidates(data: dict[str, Any], provider: str) -> list[str]:
    paths = (("basicInfo", "accountId"), ("basicInfo", "account_id"), ("basicInfo", "uid"), ("accountId",), ("account_id",), ("uid",), ("playerId",), ("player_id",)) if provider == "player" else (("uid",), ("accountId",), ("account_id",), ("playerId",), ("player_id",), ("basicInfo", "accountId"))
    candidates: list[str] = []
    for path in paths:
        value = normalize_uid(get_nested(data, *path))
        if value and value not in candidates:
            candidates.append(value)
    return candidates

def response_matches_requested_uid(data: dict[str, Any], requested_uid: str, provider: str) -> bool:
    return normalize_uid(requested_uid) in response_uid_candidates(data, provider)

async def get_session() -> aiohttp.ClientSession:
    global HTTP_SESSION
    if HTTP_SESSION is None or getattr(HTTP_SESSION, "closed", True):
        connector = aiohttp.TCPConnector(limit=100, limit_per_host=30, ttl_dns_cache=300, keepalive_timeout=30, enable_cleanup_closed=True)
        HTTP_SESSION = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT, connector=connector, headers={"Accept": "application/json", "Cache-Control": "no-cache", "Pragma": "no-cache"})
    return HTTP_SESSION

async def fetch_json(session: aiohttp.ClientSession, url: str, params: dict[str, Any]) -> dict[str, Any]:
    async with session.get(url, params=params, headers={"Cache-Control": "no-cache, no-store, max-age=0", "Pragma": "no-cache"}) as response:
        if response.status != 200:
            raise RuntimeError(f"Provider returned HTTP {response.status}")
        data = await response.json(content_type=None)
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected provider response")
        return data

async def fetch_verified_json(session: aiohttp.ClientSession, url: str, params: dict[str, Any], requested_uid: str, provider: str, attempts: int = MAX_PROVIDER_ATTEMPTS) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        request_params = dict(params)
        request_params["_cb"] = f"{time.time_ns()}_{attempt}"
        try:
            data = await fetch_json(session, url, request_params)
            if response_matches_requested_uid(data, requested_uid, provider):
                return data
            last_error = RuntimeError(f"{provider} UID mismatch")
        except Exception as error:
            last_error = error
        if attempt + 1 < attempts:
            await asyncio.sleep(RETRY_DELAY)
    raise RuntimeError(f"{provider} failed verification") from last_error

async def _lookup_player_uncached(uid: str) -> dict[str, Any]:
    session = await get_session()
    player_task = asyncio.create_task(fetch_verified_json(session, PLAYER_API, {"uid": uid}, uid, "player"))
    ban_task = asyncio.create_task(fetch_verified_json(session, BAN_API, {"key": BAN_KEY, "uid": uid}, uid, "ban"))
    player_result, ban_result = await asyncio.gather(player_task, ban_task, return_exceptions=True)
    
    if not isinstance(player_result, dict):
        raise RuntimeError("Verified player data unavailable")
        
    player = player_result
    ban = ban_result if isinstance(ban_result, dict) else {}
    basic_info = player.get("basicInfo", {})
    if not isinstance(basic_info, dict):
        basic_info = {}
        
    raw_status = str(ban.get("status", "")).upper()
    is_banned = ban.get("is_banned")
    ban_status = "🔴 BANNED" if (is_banned is True or raw_status == "BANNED") else ("🟢 NOT BANNED" if (is_banned is False or raw_status in ("NOT BANNED", "LIVE", "SAFE", "UNBANNED")) else "⚪ UNKNOWN")
    
    return {
        "uid": uid,
        "player_name": next((x for x in [basic_info.get("nickname"), player.get("nickname")] if x), None),
        "level": next((x for x in [basic_info.get("level"), player.get("AccountLevel"), player.get("level")] if x is not None), None),
        "region": next((x for x in [basic_info.get("region"), player.get("region")] if x), None),
        "likes": next((x for x in [basic_info.get("liked"), basic_info.get("likes")] if x is not None), None),
        "ban_status": ban_status,
        "is_banned": (is_banned is True or raw_status == "BANNED"),
    }

async def lookup_player(uid: str, force_refresh: bool = False) -> dict[str, Any]:
    cached = CACHE.get(uid)
    if not force_refresh and cached and time.monotonic() - cached[0] < CACHE_TTL:
        return cached[1]
    existing = INFLIGHT.get(uid)
    if existing and not existing.done():
        return await asyncio.shield(existing)
    task = asyncio.create_task(_lookup_player_uncached(uid))
    INFLIGHT[uid] = task
    try:
        result = await asyncio.shield(task)
        CACHE[uid] = (time.monotonic(), result)
        return result
    finally:
        if INFLIGHT.get(uid) is task:
            INFLIGHT.pop(uid, None)
def format_result(data: dict[str, Any]) -> str:
    return (
        "⚡ <b>APEX PLAYER INTEL HUB</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>Player Name:</b> <code>{safe_value(data.get('player_name'))}</code>\n"
        f"🆔 <b>UID:</b> <code>{safe_value(data.get('uid'))}</code>\n"
        f"⭐ <b>Level:</b> <code>{safe_value(data.get('level'))}</code>\n"
        f"🌍 <b>Region:</b> <code>{safe_value(data.get('region'))}</code>\n"
        f"❤️ <b>Likes:</b> <code>{safe_value(data.get('likes'))}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n🛡️ <b>SECURITY & BAN STATUS</b>\n\n"
        f"<b>{data.get('ban_status', '⚪ UNKNOWN')}</b>\n\n━━━━━━━━━━━━━━━━━━\n🚀 <i>Powered by Apex X Forge</i>"
    )

def format_group_result(data: dict[str, Any]) -> str:
    return (
        f"🎮 <b>PLAYER INFO</b>\n\n👤 <b>{safe_value(data.get('player_name'))}</b>\n"
        f"🆔 <code>{safe_value(data.get('uid'))}</code>\n⭐ Level: <b>{safe_value(data.get('level'))}</b> • 🌍 {safe_value(data.get('region'))}\n"
        f"❤️ Likes: <b>{safe_value(data.get('likes'))}</b>\n\n🛡️ <b>{data.get('ban_status', '⚪ UNKNOWN')}</b>"
    )

async def delete_after_delay(message: Message, seconds: int = 7):
    await asyncio.sleep(seconds)
    try:
        await message.delete()
    except Exception:
        pass

def save_history(user_id: int, data: dict[str, Any]):
    history = load_json(HISTORY_FILE, {})
    entries = history.get(str(user_id), [])
    new_entry = {"uid": str(data.get("uid", "")), "player_name": str(data.get("player_name") or "Unknown"), "region": str(data.get("region") or "N/A"), "time": int(time.time())}
    entries = [item for item in entries if item.get("uid") != new_entry["uid"]]
    entries.insert(0, new_entry)
    history[str(user_id)] = entries[:HISTORY_LIMIT]
    save_json(HISTORY_FILE, history)

def update_stats(message: Message):
    stats = load_json(STATS_FILE, {"total_searches": 0, "private_searches": 0, "group_searches": 0, "known_users": [], "known_groups": []})
    stats["total_searches"] += 1
    if is_private(message):
        stats["private_searches"] += 1
        if message.from_user.id not in stats["known_users"]:
            stats["known_users"].append(message.from_user.id)
    elif is_group(message):
        stats["group_searches"] += 1
        if message.chat.id not in stats["known_groups"]:
            stats["known_groups"].append(message.chat.id)
    save_json(STATS_FILE, stats)

async def perform_lookup(message: Message, uid: str, force_refresh: bool = False):
    loading = await message.reply(f"🔎 <b>Searching...</b> <code>{uid}</code>" if is_group(message) else f"⚡ <b>Querying Database...</b>\n\n🆔 <code>{uid}</code>")
    try:
        data = await lookup_player(uid, force_refresh=force_refresh)
        if not data.get("player_name") and not data.get("level") and not data.get("region"):
            await loading.edit_text("❌ <b>PLAYER NOT FOUND</b>\n\nPlease verify the UID.")
            if is_group(message):
                asyncio.create_task(delete_after_delay(loading, 8))
            return
        update_stats(message)
        if message.from_user:
            save_history(message.from_user.id, data)
        if is_group(message):
            await loading.edit_text(format_group_result(data))
            asyncio.create_task(delete_after_delay(loading, 15))
            return
        await loading.edit_text(format_result(data), reply_markup=result_keyboard(str(data.get("uid") or uid)))
    except Exception:
        await loading.edit_text("⚠️ <b>LOOKUP FAILED</b>\n\nTry again later.")
        if is_group(message):
            asyncio.create_task(delete_after_delay(loading, 8))

@router.message(CommandStart())
async def start_command(message: Message, bot: Bot):
    if not is_private(message):
        await message.reply("🎮 <b>APEX DATA BOT</b>\n\nUse: <code>/check PLAYER_UID</code>")
        return
    verified, _ = await check_membership(bot, message.from_user.id)
    if not verified:
        await message.answer("┏ 🛡️ <b>SECURE ACCESS GATEWAY</b>\n┣ ⚡ <b>Status:</b> Locked ❌\n┗━━━━━━━━━━━━━━━━━━\n\n✨ <b>Unlock Premium Bot Features!</b>\n\nTo use <b>APEX DATA</b>, please join our official update channel below.", reply_markup=verify_keyboard())
        return
    await message.answer(f"⚡ <b>APEX FF PLAYER INFO BOT</b>\n━━━━━━━━━━━━━━━━━━━━\n\n✅ <b>VERIFIED • ACCESS UNLOCKED</b>\n\nHey, <b>{html.escape(message.from_user.full_name)}</b>! 👋\nWelcome to the APEX Player Intelligence Hub.", reply_markup=home_keyboard())

@router.callback_query(F.data == "verify_join")
async def verify_join_callback(callback: CallbackQuery, bot: Bot):
    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer()
        return
    verified, _ = await check_membership(bot, callback.from_user.id)
    if not verified:
        await callback.answer("❌ You haven't joined the channel yet!", show_alert=True)
        return
    await callback.answer("✅ Verification Successful!", show_alert=False)
    users = load_json(USERS_FILE, {})
    users[str(callback.from_user.id)] = {"verified": True, "username": callback.from_user.username, "name": callback.from_user.full_name, "updated_at": int(time.time())}
    save_json(USERS_FILE, users)
    await callback.message.edit_text(f"⚡ <b>APEX FF PLAYER INFO BOT</b>\n━━━━━━━━━━━━━━━━━━━━\n\n✅ <b>VERIFIED • ACCESS UNLOCKED</b>", reply_markup=home_keyboard())

@router.callback_query(F.data == "new_search")
async def new_search_callback(callback: CallbackQuery, bot: Bot):
    verified, _ = await check_membership(bot, callback.from_user.id)
    if not verified:
        await callback.message.edit_text("🔐 <b>ACCESS REQUIRED</b>", reply_markup=verify_keyboard())
        await callback.answer("Join required channels first!", show_alert=True)
        return
    await callback.message.edit_text("🔎 <b>PLAYER LOOKUP</b>\n\nSend numeric Player UID to proceed.", reply_markup=home_keyboard())
    await callback.answer()

@router.callback_query(F.data == "home")
async def home_callback(callback: CallbackQuery):
    await callback.message.edit_text("🏠 <b>APEX DATA HOME</b>\n\nSend UID to check.", reply_markup=home_keyboard())
    await callback.answer()

@router.callback_query(F.data == "status")
async def status_callback(callback: CallbackQuery):
    await callback.message.edit_text("📊 <b>SYSTEM STATUS</b>\n\n🤖 Bot: 🟢 Online & Stable", reply_markup=home_keyboard())
    await callback.answer("System is running smoothly!", show_alert=True)

@router.message(Command("check"))
async def group_check_command(message: Message):
    if is_private(message) and not await require_private_access(message, message.bot):
        return
    if not message.text:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply("Use: <code>/check YOUR_UID</code>")
        return
    uid = parts[1].strip()
    if not valid_uid(uid):
        await message.reply("⚠️ Invalid UID format.")
        return
    await perform_lookup(message, uid, force_refresh=True)

@router.message(F.text)
async def private_uid_handler(message: Message):
    if not is_private(message) or message.text.startswith("/"):
        return
    if not await require_private_access(message, message.bot):
        return
    text = message.text.strip()
    if not valid_uid(text):
        await message.answer("⚠️ Please send a valid numeric UID.")
        return
    await perform_lookup(message, text, force_refresh=True)

async def main():
    ensure_files()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    logging.info("APEX DATA BOT STARTED")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if HTTP_SESSION and not getattr(HTTP_SESSION, "closed", True):
            await HTTP_SESSION.close()
        await bot.session.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
                    