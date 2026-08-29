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

REQUEST_TIMEOUT = aiohttp.ClientTimeout(
    total=4,
    connect=2,
    sock_connect=2,
    sock_read=3,
)
CACHE_TTL = 60
HISTORY_LIMIT = 10
MAX_PROVIDER_ATTEMPTS = 2
RETRY_DELAY = 0.12

BASE_DIR = Path(__file__).resolve().parent
REQUIRED_CHANNELS_FILE = BASE_DIR / "channels.txt"
DATA_DIR = BASE_DIR / "data"
USERS_FILE = DATA_DIR / "users.json"
HISTORY_FILE = DATA_DIR / "history.json"
STATS_FILE = DATA_DIR / "stats.json"

router = Router()

CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
INFLIGHT: dict[str, asyncio.Task[dict[str, Any]]] = {}
HTTP_SESSION: aiohttp.ClientSession | None = None


# ============================================================
#                       FILE STORAGE
# ============================================================

def ensure_files():
    DATA_DIR.mkdir(exist_ok=True)
    if not USERS_FILE.exists():
        USERS_FILE.write_text("{}", encoding="utf-8")
    if not HISTORY_FILE.exists():
        HISTORY_FILE.write_text("{}", encoding="utf-8")
    if not STATS_FILE.exists():
        STATS_FILE.write_text(
            json.dumps(
                {
                    "total_searches": 0,
                    "private_searches": 0,
                    "group_searches": 0,
                    "known_users": [],
                    "known_groups": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    if not REQUIRED_CHANNELS_FILE.exists():
        REQUIRED_CHANNELS_FILE.write_text(
            "https://t.me/YourChannel\n"
            "https://t.me/YourGroup\n",
            encoding="utf-8",
        )


def load_json(path: Path, default: Any):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data: Any):
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


# ============================================================
#                REQUIRED CHANNELS CONFIG
# ============================================================

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


# ============================================================
#                       UI KEYBOARDS
# ============================================================

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
        buttons.append([InlineKeyboardButton(text=f"📢 JOIN CHANNEL {index}", url=item["link"])])
    buttons.append([InlineKeyboardButton(text="⚡ VERIFY & UNLOCK ACCESS", callback_data="verify_join")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============================================================
#                     BASIC UTILITIES
# ============================================================

def safe_value(value: Any, default: str = "N/A") -> str:
    if value is None or value == "":
        return default
    return html.escape(str(value))


def first_value(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def valid_uid(uid: str) -> bool:
    return uid.isdigit() and 5 <= len(uid) <= 15


def is_private(message: Message) -> bool:
    return message.chat.type == ChatType.PRIVATE


def is_group(message: Message) -> bool:
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


# ============================================================
#                    JOIN VERIFICATION
# ============================================================

async def check_membership(bot: Bot, user_id: int) -> tuple[bool, list[str]]:
    required = load_required_channels()
    if not required:
        return True, []

    async def check_one(item: dict[str, str]):
        ref = item["chat_id"]

        async def is_valid_member(chat_target):
            member = await bot.get_chat_member(chat_id=chat_target, user_id=user_id)
            status = getattr(member.status, "value", member.status)
            status = str(status).lower()
            active_statuses = {"creator", "owner", "administrator", "member"}
            if status in active_statuses:
                return True
            if status == "restricted":
                return bool(getattr(member, "is_member", False))
            return False

        try:
            if await is_valid_member(ref):
                return None
        except Exception:
            pass

        try:
            chat = await bot.get_chat(ref)
            if await is_valid_member(chat.id):
                return None
        except Exception:
            pass

        return item["link"]

    results = await asyncio.gather(*(check_one(item) for item in required))
    missing = [item for item in results if item is not None]
    return len(missing) == 0, missing


async def require_private_access(message: Message, bot: Bot) -> bool:
    if not is_private(message):
        return True

    verified, _ = await check_membership(bot, message.from_user.id)
    if verified:
        users = load_json(USERS_FILE, {})
        users[str(message.from_user.id)] = {
            "verified": True,
            "username": message.from_user.username,
            "name": message.from_user.full_name,
            "updated_at": int(time.time()),
        }
        save_json(USERS_FILE, users)
        return True

    await message.answer(
        "┏ 🛡️ <b>ACCESS CONTROL SYSTEM</b>\n"
        "┣ ⚡ <b>Status:</b> Locked ❌\n"
        "┗━━━━━━━━━━━━━━━━━━\n\n"
        "✨ <b>Unlock Full Bot Privileges!</b>\n\n"
        "To use <b>APEX DATA</b>, please join our official update channels and community group below[span_0](start_span)[span_0](end_span).\n\n"
        "👇 <i>Join all links, then click verify button.</i>",
        reply_markup=verify_keyboard(),
    )
    return False
    # ============================================================
#                     HTTP LOOKUP ENGINE
# ============================================================

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
    if provider == "player":
        paths = (
            ("basicInfo", "accountId"),
            ("basicInfo", "account_id"),
            ("basicInfo", "uid"),
            ("accountId",),
            ("account_id",),
            ("uid",),
            ("playerId",),
            ("player_id",),
        )
    else:
        paths = (
            ("uid",),
            ("accountId",),
            ("account_id",),
            ("playerId",),
            ("player_id",),
            ("basicInfo", "accountId"),
        )

    candidates: list[str] = []
    for path in paths:
        value = normalize_uid(get_nested(data, *path))
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def response_matches_requested_uid(data: dict[str, Any], requested_uid: str, provider: str) -> bool:
    requested = normalize_uid(requested_uid)
    return requested in response_uid_candidates(data, provider)


async def get_session() -> aiohttp.ClientSession:
    global HTTP_SESSION
    if HTTP_SESSION is None or HTTP_SESSION.closed:
        connector = aiohttp.TCPConnector(
            limit=100, limit_per_host=30, ttl_dns_cache=600, keepalive_timeout=60, enable_cleanup_closed=True
        )
        HTTP_SESSION = aiohttp.ClientSession(
            timeout=REQUEST_TIMEOUT,
            connector=connector,
            headers={"Accept": "application/json", "Cache-Control": "no-cache", "Pragma": "no-cache"},
        )
    return HTTP_SESSION


async def fetch_json(session: aiohttp.ClientSession, url: str, params: dict[str, Any]) -> dict[str, Any]:
    async with session.get(url, params=params, headers={"Cache-Control": "no-cache, no-store, max-age=0", "Pragma": "no-cache"}) as response:
        if response.status != 200:
            raise RuntimeError(f"Provider returned HTTP {response.status}")
        data = await response.json(content_type=None)
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected provider response")
        return data


async def fetch_verified_json(
    session: aiohttp.ClientSession, url: str, params: dict[str, Any], requested_uid: str, provider: str, attempts: int = MAX_PROVIDER_ATTEMPTS
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        request_params = dict(params)
        request_params["_cb"] = f"{time.time_ns()}_{attempt}"
        try:
            data = await fetch_json(session, url, request_params)
            if response_matches_requested_uid(data, requested_uid, provider):
                return data
            returned = response_uid_candidates(data, provider)
            last_error = RuntimeError(f"{provider} UID mismatch: requested={requested_uid}, returned={returned or 'missing'}")
        except Exception as error:
            last_error = error
        if attempt + 1 < attempts:
            await asyncio.sleep(RETRY_DELAY)
    raise RuntimeError(f"{provider} provider failed verification") from last_error


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

    if is_banned is True or raw_status == "BANNED":
        ban_status = "🔴 BANNED"
    elif is_banned is False or raw_status in ("NOT BANNED", "LIVE", "SAFE", "UNBANNED"):
        ban_status = "🟢 NOT BANNED"
    else:
        ban_status = "⚪ UNKNOWN"

    result = {
        "uid": uid,
        "player_name": first_value(basic_info.get("nickname"), player.get("nickname")),
        "level": first_value(basic_info.get("level"), player.get("AccountLevel"), player.get("level")),
        "region": first_value(basic_info.get("region"), player.get("region")),
        "likes": first_value(basic_info.get("liked"), basic_info.get("likes"), player.get("liked"), player.get("likes")),
        "ban_status": ban_status,
        "is_banned": (is_banned is True or raw_status == "BANNED"),
        "ban_period": ban.get("ban_period"),
    }

    if not result["player_name"] and result["level"] is None and not result["region"]:
        raise RuntimeError("Verified player response contains no profile data")
    return result


async def lookup_player(uid: str, force_refresh: bool = False) -> dict[str, Any]:
    cached = CACHE.get(uid)
    now = time.monotonic()
    if not force_refresh and cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    existing = INFLIGHT.get(uid)
    if existing is not None and not existing.done():
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


# ============================================================
#                      RESULT FORMAT & HANDLERS
# ============================================================

def format_result(data: dict[str, Any]) -> str:
    return (
        "🎮 <b>APEX PLAYER INFO</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 <b>Player Name:</b> <code>{safe_value(data.get('player_name'))}</code>\n"
        f"🆔 <b>UID:</b> <code>{safe_value(data.get('uid'))}</code>\n"
        f"⭐ <b>Level:</b> <code>{safe_value(data.get('level'))}</code>\n"
        f"🌍 <b>Region:</b> <code>{safe_value(data.get('region'))}</code>\n"
        f"❤️ <b>Likes:</b> <code>{safe_value(data.get('likes'))}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🛡️ <b>BAN STATUS</b>\n\n"
        f"<b>{data.get('ban_status', '⚪ UNKNOWN')}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚡ <i>Apex Data Intelligence</i>"
    )


def format_group_result(data: dict[str, Any]) -> str:
    return (
        "🎮 <b>PLAYER INFO</b>\n\n"
        f"👤 <b>{safe_value(data.get('player_name'))}</b>\n"
        f"🆔 <code>{safe_value(data.get('uid'))}</code>\n"
        f"⭐ Level: <b>{safe_value(data.get('level'))}</b> • "
        f"🌍 {safe_value(data.get('region'))}\n"
        f"❤️ Likes: <b>{safe_value(data.get('likes'))}</b>\n\n"
        f"🛡️ <b>{data.get('ban_status', '⚪ UNKNOWN')}</b>"
    )


async def delete_after_delay(message: Message, seconds: int = 7):
    await asyncio.sleep(seconds)
    try:
        await message.delete()
    except Exception:
        pass


def save_history(user_id: int, data: dict[str, Any]):
    history = load_json(HISTORY_FILE, {})
    key = str(user_id)
    entries = history.get(key, [])
    new_entry = {
        "uid": str(data.get("uid", "")),
        "player_name": str(data.get("player_name") or "Unknown"),
        "region": str(data.get("region") or "N/A"),
        "time": int(time.time()),
    }
    entries = [item for item in entries if item.get("uid") != new_entry["uid"]]
    entries.insert(0, new_entry)
    history[key] = entries[:HISTORY_LIMIT]
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
    loading = await message.reply(
        f"🔎 <b>Searching...</b> <code>{uid}</code>" if is_group(message) else f"🔎 <b>Searching Player...</b>\n\n🆔 <code>{uid}</code>",
        reply_markup=None,
    )
    try:
        data = await lookup_player(uid, force_refresh=force_refresh)
        if not data.get("player_name") and not data.get("level") and not data.get("region"):
            await loading.edit_text("❌ <b>PLAYER NOT FOUND</b>\n\nPlease check the UID and try again.")
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
        await loading.edit_text("⚠️ <b>DATA CHECK FAILED</b>\n\nPlease try again.")
        if is_group(message):
            asyncio.create_task(delete_after_delay(loading, 8))


@router.message(CommandStart())
async def start_command(message: Message, bot: Bot):
    if not is_private(message):
        await message.reply("🎮 <b>APEX DATA BOT</b>\n\nUse: <code>/check PLAYER_UID</code>")
        return

    verified, _ = await check_membership(bot, message.from_user.id)
    if not verified:
        await message.answer(
            "┏ 🛡️ <b>ACCESS CONTROL SYSTEM</b>\n"
            "┣ ⚡ <b>Status:</b> Locked ❌\n"
            "┗━━━━━━━━━━━━━━━━━━\n\n"
            "✨ <b>Unlock Full Bot Privileges!</b>\n\n"
            "To use <b>APEX DATA</b>, please join our official update channels and community group below[span_1](start_span)[span_1](end_span).\n\n"
            "👇 <i>Join all links, then click verify button.</i>",
            reply_markup=verify_keyboard(),
        )
        return

    user_name = html.escape(message.from_user.full_name)
    await message.answer(
        f"┏ 🌟 <b>WELCOME, {user_name}</b>\n"
        f"┣ 🤖 <b>Bot:</b> Apex Data Intelligence\n"
        f"┗━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ <b>Next-Gen Free Fire Information Hub</b>\n\n"
        "╭ 🔎 <b>Player Lookup</b>\n"
        "┆ <i>Get instant detailed profile & stats</i>\n"
        "├ 🛡️ <b>Ban Status Check</b>\n"
        "┆ <i>Verify current account status securely</i>\n"
        "╰ 🟢 <b>System Status</b>\n"
        "    <i>High-speed API connection active</i>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💬 <i>Send your Player UID below to start!</i>",
        reply_markup=home_keyboard(),
    )


@router.callback_query(F.data == "verify_join")
async def verify_join_callback(callback: CallbackQuery, bot: Bot):
    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer()
        return

    verified, missing = await check_membership(bot, callback.from_user.id)
    if not verified:
        await callback.message.edit_text(
            "┏ ⚠️ <b>VERIFICATION INCOMPLETE</b>\n"
            "┣ ❌ <b>Status:</b> Missing Joins\n"
            "┗━━━━━━━━━━━━━━━━━━\n\n"
            f"You haven't joined all required chats yet!\n"
            f"📌 Remaining Channels: <b>{len(missing)}</b>\n\n"
            "👇 <i>Please join them and tap verify again.</i>",
            reply_markup=verify_keyboard(),
        )
        return

    users = load_json(USERS_FILE, {})
    users[str(callback.from_user.id)] = {
        "verified": True,
        "username": callback.from_user.username,
        "name": callback.from_user.full_name,
        "updated_at": int(time.time()),
    }
    save_json(USERS_FILE, users)

    user_name = html.escape(callback.from_user.full_name)
    await callback.message.edit_text(
        f"┏ 🌟 <b>WELCOME, {user_name}</b>\n"
        f"┣ 🤖 <b>Bot:</b> Apex Data Intelligence\n"
        f"┗━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ <b>Next-Gen Free Fire Information Hub</b>\n\n"
        "╭ 🔎 <b>Player Lookup</b>\n"
        "┆ <i>Get instant detailed profile & stats</i>\n"
        "├ 🛡️ <b>Ban Status Check</b>\n"
        "┆ <i>Verify current account status securely</i>\n"
        "╰ 🟢 <b>System Status</b>\n"
        "    <i>High-speed API connection active</i>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💬 <i>Send your Player UID below to start!</i>",
        reply_markup=home_keyboard(),
    )


@router.callback_query(F.data == "new_search")
async def new_search_callback(callback: CallbackQuery, bot: Bot):
    verified, _ = await check_membership(bot, callback.from_user.id)
    if not verified:
        await callback.message.edit_text("🔐 <b>ACCESS REQUIRED</b>", reply_markup=verify_keyboard())
        await callback.answer()
        return
    await callback.message.edit_text("🔎 <b>CHECK PLAYER</b>\n\nSend numeric Player UID.", reply_markup=home_keyboard())
    await callback.answer()


@router.callback_query(F.data == "home")
async def home_callback(callback: CallbackQuery):
    await callback.message.edit_text("🏠 <b>APEX DATA HOME</b>\n\nSend UID to check.", reply_markup=home_keyboard())
    await callback.answer()


@router.callback_query(F.data == "status")
async def status_callback(callback: CallbackQuery):
    await callback.message.edit_text("📊 <b>SYSTEM STATUS</b>\n\n🤖 Bot: 🟢 Online", reply_markup=home_keyboard())
    await callback.answer()


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
    await perform_lookup(message, uid)


@router.message(F.text)
async def private_uid_handler(message: Message):
    if not is_private(message) or message.text.startswith("/"):
        return
    if not await require_private_access(message, message.bot):
        return
    text = message.text.strip()
    if not valid_uid(text):
        await message.answer("⚠️ Send only numeric UID.")
        return
    await perform_lookup(message, text)


async def main():
    ensure_files()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    logging.info("APEX DATA BOT STARTED")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if HTTP_SESSION and not HTTP_SESSION.closed:
            await HTTP_SESSION.close()
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
        