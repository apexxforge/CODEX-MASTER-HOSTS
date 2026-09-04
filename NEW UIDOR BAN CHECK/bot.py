import asyncio
import html
import logging
import re
from pathlib import Path

import aiohttp

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType, ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = "8638501669:AAHUgxWcM5RRgiaDWw6w0pz5bvpUPH4DdOo"
API_URL = "http://13.48.49.51:5001/Bmw"

OWNER = "@ApexXForge"
DEVELOPER = "@ApexXForge"

CHANNELS_FILE = "channels.txt"
GROUP_DELETE_TIME = 45

SUPPORTED_REGIONS = [
    "IND", "BR", "US", "SAC", "NA",
    "SG", "RU", "ID", "TW", "VN",
    "TH", "ME", "PK", "BD", "EUROPE"
]


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)
http_session = None


# =========================================================
# HTTP SESSION
# =========================================================

async def post_init(application: Application):
    global http_session

    timeout = aiohttp.ClientTimeout(total=20, connect=5)
    connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)

    http_session = aiohttp.ClientSession(
        timeout=timeout,
        connector=connector
    )


async def post_shutdown(application: Application):
    global http_session

    if http_session:
        await http_session.close()


# =========================================================
# DATA HELPERS
# =========================================================

def normalize_key(key):
    return (
        str(key).lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )


def find_value(data, possible_keys):

    normalized_keys = {
        normalize_key(key)
        for key in possible_keys
    }

    if isinstance(data, dict):

        for key, value in data.items():
            if normalize_key(key) in normalized_keys and value is not None:
                return value

        for value in data.values():
            result = find_value(value, possible_keys)
            if result is not None:
                return result

    elif isinstance(data, list):

        for item in data:
            result = find_value(item, possible_keys)
            if result is not None:
                return result

    return None


# =========================================================
# CHANNELS.TXT
#
# One public channel per line.
# Supported:
# @username
# https://t.me/username
# t.me/username
# =========================================================

def load_channels():

    path = Path(CHANNELS_FILE)

    if not path.exists():
        return []

    channels = []

    try:
        for line in path.read_text(
            encoding="utf-8"
        ).splitlines():

            value = line.strip()

            if value and not value.startswith("#"):
                channels.append(value)

    except Exception as error:
        logger.error("channels.txt error: %s", error)

    return channels


def get_channel_username(channel):

    value = channel.strip()

    value = re.sub(
        r"^https?://",
        "",
        value,
        flags=re.IGNORECASE
    )

    value = re.sub(
        r"^t\.me/",
        "",
        value,
        flags=re.IGNORECASE
    )

    value = value.split("?")[0].strip("/")

    if not value.startswith("@"):
        value = "@" + value

    return value


def get_channel_url(channel):

    value = channel.strip()

    if value.startswith(("https://", "http://")):
        return value

    if value.startswith("t.me/"):
        return "https://" + value

    return "https://t.me/" + value.lstrip("@")


# =========================================================
# FORCE JOIN
# =========================================================

async def check_force_join(update, context):

    user = update.effective_user
    channels = load_channels()

    if not channels:
        return True, []

    missing = []

    for channel in channels:

        try:
            member = await context.bot.get_chat_member(
                chat_id=get_channel_username(channel),
                user_id=user.id
            )

            if member.status in ("left", "kicked"):
                missing.append(channel)

        except Exception as error:
            logger.warning(
                "Verification lookup failed for %s: %s",
                channel,
                error
            )

            # Avoid false denial caused by Telegram lookup errors.
            continue

    return len(missing) == 0, missing


def force_join_keyboard(channels):

    rows = []

    for index, channel in enumerate(channels, start=1):
        rows.append([
            InlineKeyboardButton(
                f"📢 Join Channel {index}",
                url=get_channel_url(channel)
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "♻️ Verify Membership",
            callback_data="verify_join"
        )
    ])

    return InlineKeyboardMarkup(rows)


async def send_force_join_message(update, channels):

    text = (
        "🟢 <b>ACCESS VERIFICATION REQUIRED</b>\n\n"
        "Please join all required channels first.\n\n"
        "After joining, click <b>Verify Membership</b>."
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=force_join_keyboard(channels)
    )


async def verify_join_callback(update, context):

    query = update.callback_query

    joined, missing = await check_force_join(
        update,
        context
    )

    if not joined:

        await query.answer(
            "⚠️ Please join all channels first!",
            show_alert=True
        )

        try:
            await query.edit_message_reply_markup(
                reply_markup=force_join_keyboard(missing)
            )
        except Exception:
            pass

        return

    await query.answer(
        "✅ Verification successful!",
        show_alert=True
    )

    text = (
        "🟢 <b>ACCESS VERIFIED</b>\n\n"
        "You can now use the bot.\n\n"
        "<code>/get UID REGION</code>\n\n"
        "Example: <code>/get 11563200119 IND</code>\n\n"
        "━━━━━━━━━━━━━━━\n"
        f"💬 Dm {OWNER}"
    )

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML
    )


# =========================================================
# MESSAGE HELPERS
# =========================================================

async def delete_later(message, delay):

    try:
        await asyncio.sleep(delay)
        await message.delete()
    except Exception:
        pass


def format_duration(value):

    text = str(value).strip()

    if text.upper() == "N/A":
        return text

    replacements = [
        (r"(\d+)\s+days?", r"\1d"),
        (r"(\d+)\s+hours?", r"\1h"),
        (r"(\d+)\s+minutes?", r"\1m"),
        (r"(\d+)\s+seconds?", r"\1s"),
    ]

    for pattern, replacement in replacements:
        text = re.sub(
            pattern,
            replacement,
            text,
            flags=re.IGNORECASE
        )

    text = text.replace(",", " ")
    text = re.sub(r"\s+", " ", text).strip()

    return text


# =========================================================
# START
# =========================================================

async def start(update, context):

    if update.effective_chat.type == ChatType.PRIVATE:

        joined, missing = await check_force_join(
            update,
            context
        )

        if not joined:
            await send_force_join_message(update, missing)
            return

    text = (
        "🟢 <b>FREE FIRE PLAYER INFO</b>\n\n"
        "Use:\n"
        "<code>/get UID REGION</code>\n\n"
        "Example:\n"
        "<code>/get 11563200119 IND</code>\n\n"
        "━━━━━━━━━━━━━━━\n"
        f"👑 Owner: {OWNER}\n"
        f"💻 Developer: {DEVELOPER}"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML
    )


# =========================================================
# HELP
# =========================================================

async def help_command(update, context):

    if update.effective_chat.type == ChatType.PRIVATE:

        joined, missing = await check_force_join(
            update,
            context
        )

        if not joined:
            await send_force_join_message(update, missing)
            return

    text = (
        "🟢 <b>COMMAND GUIDE</b>\n\n"
        "<code>/get UID REGION</code>\n\n"
        "Example:\n"
        "<code>/get 11563200119 IND</code>\n\n"
        "🌍 <b>Supported Regions</b>\n"
        "<code>IND • BR • US • SAC • NA</code>\n"
        "<code>SG • RU • ID • TW • VN</code>\n"
        "<code>TH • ME • PK • BD • EUROPE</code>"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML
    )


# =========================================================
# GET PLAYER
# =========================================================

async def get_player(update, context):

    message = update.effective_message
    chat_type = update.effective_chat.type


    # Force join only for private usage.
    if chat_type == ChatType.PRIVATE:

        joined, missing = await check_force_join(
            update,
            context
        )

        if not joined:
            await send_force_join_message(update, missing)
            return


    # Validate command.
    if len(context.args) < 2:

        result = await message.reply_text(
            "⚠️ <b>INVALID FORMAT</b>\n\n"
            "Use: <code>/get UID REGION</code>\n\n"
            "Example: <code>/get 11563200119 IND</code>",
            parse_mode=ParseMode.HTML
        )

        if chat_type != ChatType.PRIVATE:
            asyncio.create_task(delete_later(result, 20))

        return


    uid = context.args[0].strip()
    region = context.args[1].strip().upper()


    if not uid.isdigit():

        result = await message.reply_text(
            "❌ <b>INVALID UID</b>\n\n"
            "UID must contain numbers only.",
            parse_mode=ParseMode.HTML
        )

        if chat_type != ChatType.PRIVATE:
            asyncio.create_task(delete_later(result, 15))

        return


    if region not in SUPPORTED_REGIONS:

        result = await message.reply_text(
            "⚠️ <b>INVALID REGION</b>\n\n"
            "<code>IND • BR • US • SAC • NA</code>\n"
            "<code>SG • RU • ID • TW • VN</code>\n"
            "<code>TH • ME • PK • BD • EUROPE</code>",
            parse_mode=ParseMode.HTML
        )

        if chat_type != ChatType.PRIVATE:
            asyncio.create_task(delete_later(result, 20))

        return


    loading = await message.reply_text(
        "🔍 <b>Fetching player information...</b>",
        parse_mode=ParseMode.HTML
    )


    try:

        async with http_session.get(
            API_URL,
            params={
                "uid": uid,
                "reg": region
            }
        ) as response:

            logger.info(
                "UID=%s REGION=%s STATUS=%s",
                uid,
                region,
                response.status
            )

            if response.status != 200:

                await loading.edit_text(
                    f"❌ API Error: <code>{response.status}</code>",
                    parse_mode=ParseMode.HTML
                )

                return

            data = await response.json(
                content_type=None
            )


        if not isinstance(data, (dict, list)):

            await loading.edit_text(
                "❌ Invalid API response."
            )

            return


        nickname = find_value(
            data,
            ["Nickname", "nickname", "Name", "PlayerName"]
        )

        player_uid = find_value(
            data,
            ["Uid", "UID", "uid", "PlayerUID"]
        )

        player_region = find_value(
            data,
            ["Region", "region", "Server"]
        )

        level = find_value(
            data,
            ["Level", "level", "PlayerLevel"]
        )

        state = find_value(
            data,
            [
                "State",
                "Status",
                "BanStatus",
                "AccountStatus",
                "Ban State"
            ]
        )

        since = find_value(
            data,
            ["Since", "BanSince", "BannedSince"]
        )

        unban_time = find_value(
            data,
            [
                "Unban Time",
                "UnbanTime",
                "Unban_Time",
                "Remaining Time",
                "RemainingTime"
            ]
        )


        nickname = nickname if nickname is not None else "N/A"
        player_uid = player_uid if player_uid is not None else uid
        player_region = player_region if player_region is not None else region
        level = level if level is not None else "N/A"
        state = state if state is not None else "N/A"
        since = since if since is not None else "N/A"
        unban_time = unban_time if unban_time is not None else "N/A"


        since = format_duration(since)
        unban_time = format_duration(unban_time)


        nickname = html.escape(str(nickname))
        player_uid = html.escape(str(player_uid))
        player_region = html.escape(str(player_region))
        level = html.escape(str(level))
        state = html.escape(str(state))
        since = html.escape(str(since))
        unban_time = html.escape(str(unban_time))


        state_lower = state.lower()

        if "permanent" in state_lower:
            status_icon = "🚫"

        elif "temporary" in state_lower:
            status_icon = "🔒"

        elif "not banned" in state_lower or "active" in state_lower:
            status_icon = "🟢"

        else:
            status_icon = "🔐"


        # =================================================
        # GROUP UI - FINAL APPROVED DESIGN
        # =================================================

        if chat_type != ChatType.PRIVATE:

            text = f"""🟢 <b>PLAYER INFORMATION</b>

👤 <b>Name:</b> <code>{nickname}</code>
🆔 <b>UID:</b> <code>{player_uid}</code>
🌍 <b>Region:</b> <code>{player_region}</code>
📊 <b>Level:</b> <code>{level}</code>

━━━━━━━━━━━━━━━
🔐 <b>ACCOUNT STATUS</b>
━━━━━━━━━━━━━━━

{status_icon} <b>Status:</b> <code>{state}</code>
🔓 <b>Unban:</b> <code>{unban_time}</code>
⌛ <b>Since:</b> <code>{since}</code>

━━━━━━━━━━━━━━━
💬 Dm {OWNER}"""


        # =================================================
        # PRIVATE UI - PREMIUM CLEAN DESIGN
        # =================================================

        else:

            text = f"""🟢 <b>PLAYER PROFILE FOUND</b>

━━━━━━━━━━━━━━━

👤 <b>Name:</b> <code>{nickname}</code>
🆔 <b>UID:</b> <code>{player_uid}</code>
🌍 <b>Region:</b> <code>{player_region}</code>
📊 <b>Level:</b> <code>{level}</code>

━━━━━━━━━━━━━━━
🔐 <b>ACCOUNT STATUS</b>
━━━━━━━━━━━━━━━

{status_icon} <b>Status:</b> <code>{state}</code>
🔓 <b>Unban:</b> <code>{unban_time}</code>
⌛ <b>Since:</b> <code>{since}</code>

━━━━━━━━━━━━━━━

💬 <b>Need Help?</b> Dm {OWNER}"""


        await loading.edit_text(
            text,
            parse_mode=ParseMode.HTML
        )


        # Auto-delete only the bot's group result.
        if chat_type != ChatType.PRIVATE:

            asyncio.create_task(
                delete_later(
                    loading,
                    GROUP_DELETE_TIME
                )
            )


    except asyncio.TimeoutError:

        await loading.edit_text(
            "⚠️ <b>API Timeout</b>\n\nTry again.",
            parse_mode=ParseMode.HTML
        )


    except aiohttp.ClientConnectionError:

        await loading.edit_text(
            "❌ <b>API Connection Failed</b>",
            parse_mode=ParseMode.HTML
        )


    except Exception as error:

        logger.exception(
            "Unexpected error: %s",
            error
        )

        await loading.edit_text(
            "❌ <b>Unexpected API Error</b>",
            parse_mode=ParseMode.HTML
        )


# =========================================================
# MAIN
# =========================================================

def main():

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .build()
    )


    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("help", help_command)
    )

    app.add_handler(
        CommandHandler("get", get_player)
    )

    app.add_handler(
        CallbackQueryHandler(
            verify_join_callback,
            pattern=r"^verify_join$"
        )
    )


    print("🟢 BOT ONLINE")

    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
