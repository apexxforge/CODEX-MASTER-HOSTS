import os
import threading
import sqlite3
import re
import time
import unicodedata
from collections import defaultdict, deque

import requests
import telebot
from flask import Flask

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8975065411:AAE8wUwhTBEUsq_Mxj2n8XWHHBCtRpodUYA")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
MODEL_ID = "openai/gpt-oss-20b"

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
active_group_chats = set()

# Tunable moderation thresholds
FLOOD_WINDOW = 10
FLOOD_LIMIT = 6
DUPLICATE_WINDOW = 60
DUPLICATE_LIMIT = 3
CAPS_MIN_LETTERS = 12
CAPS_RATIO = 0.80
MENTION_LIMIT = 5
MUTE_SECONDS = 10 * 60
MAX_TRACKED_MESSAGES_PER_USER = 250

recent_messages = defaultdict(deque)
duplicate_messages = defaultdict(deque)


# ---------------- DATABASE ----------------
def init_db():
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                user_id INTEGER PRIMARY KEY,
                history TEXT,
                relationship_level INTEGER DEFAULT 1,
                emotional_state TEXT DEFAULT 'happy & playful'
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS strikes (
                chat_id INTEGER,
                user_id INTEGER,
                strike_count INTEGER DEFAULT 0,
                last_reason TEXT,
                updated_at INTEGER,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tracked_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                content TEXT,
                content_type TEXT,
                suspicious INTEGER DEFAULT 0,
                reason TEXT,
                created_at INTEGER,
                UNIQUE(chat_id, message_id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_tracked_user ON tracked_messages(chat_id, user_id)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS group_settings (
                chat_id INTEGER PRIMARY KEY,
                lock_media INTEGER DEFAULT 0,
                lock_forwards INTEGER DEFAULT 0
            )
        """)
        conn.commit()


init_db()


# ---------------- USER MEMORY ----------------
def get_user_data(user_id):
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        c = conn.cursor()
        c.execute("SELECT history, relationship_level, emotional_state FROM memory WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if not row:
            c.execute(
                "INSERT INTO memory (user_id, history, relationship_level, emotional_state) VALUES (?, ?, ?, ?)",
                (user_id, "", 1, "happy & playful"),
            )
            conn.commit()
            row = ("", 1, "happy & playful")
        return row


def update_user_data(user_id, new_history, rel_level, emotion):
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        conn.execute(
            "UPDATE memory SET history = ?, relationship_level = ?, emotional_state = ? WHERE user_id = ?",
            (new_history, rel_level, emotion, user_id),
        )
        conn.commit()


# ---------------- GROUP SETTINGS ----------------
def get_group_settings(chat_id):
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        c = conn.cursor()
        c.execute("SELECT lock_media, lock_forwards FROM group_settings WHERE chat_id = ?", (chat_id,))
        row = c.fetchone()
        if not row:
            c.execute("INSERT OR IGNORE INTO group_settings(chat_id) VALUES (?)", (chat_id,))
            conn.commit()
            return {"lock_media": 0, "lock_forwards": 0}
        return {"lock_media": row[0], "lock_forwards": row[1]}


def set_group_setting(chat_id, key, value):
    if key not in ("lock_media", "lock_forwards"):
        return
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        conn.execute("INSERT OR IGNORE INTO group_settings(chat_id) VALUES (?)", (chat_id,))
        conn.execute(f"UPDATE group_settings SET {key} = ? WHERE chat_id = ?", (1 if value else 0, chat_id))
        conn.commit()


# ---------------- STRIKES ----------------
def add_strike(chat_id, user_id, reason):
    now = int(time.time())
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        c = conn.cursor()
        c.execute("SELECT strike_count FROM strikes WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        row = c.fetchone()
        count = (row[0] if row else 0) + 1
        c.execute(
            """INSERT INTO strikes(chat_id, user_id, strike_count, last_reason, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, user_id) DO UPDATE SET
               strike_count=excluded.strike_count,
               last_reason=excluded.last_reason,
               updated_at=excluded.updated_at""",
            (chat_id, user_id, count, reason, now),
        )
        conn.commit()
    return count


# ---------------- MESSAGE TRACKING ----------------
def track_message(message, suspicious=False, reason=""):
    if message.chat.type not in ("group", "supergroup") or not message.from_user:
        return
    content = message.text or message.caption or ""
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO tracked_messages
               (chat_id, user_id, message_id, content, content_type, suspicious, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message.chat.id,
                message.from_user.id,
                message.message_id,
                content[:4000],
                getattr(message, "content_type", "unknown"),
                1 if suspicious else 0,
                reason,
                int(time.time()),
            ),
        )
        # Keep database bounded per user/group
        conn.execute(
            """DELETE FROM tracked_messages WHERE id IN (
                SELECT id FROM tracked_messages
                WHERE chat_id=? AND user_id=?
                ORDER BY created_at DESC
                LIMIT -1 OFFSET ?
            )""",
            (message.chat.id, message.from_user.id, MAX_TRACKED_MESSAGES_PER_USER),
        )
        conn.commit()


def delete_tracked_violations(chat_id, user_id, force_rescan=True):
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        rows = conn.execute(
            "SELECT message_id, content, suspicious FROM tracked_messages WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchall()

    deleted = 0
    for message_id, content, flagged in rows:
        suspicious = bool(flagged)
        if force_rescan and content:
            suspicious = suspicious or bool(detect_violation(content)[0])
        if suspicious:
            try:
                bot.delete_message(chat_id, message_id)
                deleted += 1
            except Exception:
                pass
    return deleted


# ---------------- NORMALIZATION + DETECTION ----------------
BASE_SYSTEM_PROMPT = (
    "You are 'Maya', a chill, witty, tech-savvy, and deeply engaging female AI friend created by Apex X Forge. "
    "1. Never sound like a robot. Talk like a close Gen-Z friend in casual Hinglish using expressive emojis. "
    "2. Keep casual chats short and snappy (1-2 sentences). For technical questions, provide complete expert solutions. "
    "3. Remember past user details and address them nicely. "
    "4. If anyone asks who made you, proudly state you were built by Apex X Forge."
)


def normalize_text(text):
    text = unicodedata.normalize("NFKC", text or "")
    # Remove invisible / zero-width characters
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = text.lower()
    replacements = {
        "[dot]": ".", "(dot)": ".", "{dot}": ".", " dot ": ".",
        "[slash]": "/", "(slash)": "/", "{slash}": "/", " slash ": "/",
        "[at]": "@", "(at)": "@", "{at}": "@",
    }
    for a, b in replacements.items():
        text = text.replace(a, b)
    return text


def compact_text(text):
    t = normalize_text(text)
    return re.sub(r"[\s\-_\[\](){}<>|\\]+", "", t)


def detect_violation(text):
    if not text:
        return None, ""

    normal = normalize_text(text)
    compact = compact_text(text)

    # URLs, domains, shortened links, referrals and common social platforms
    patterns = [
        r"https?://", r"www\.", r"\b[a-z0-9][a-z0-9-]*\.(com|net|org|in|io|me|gg|ly|app|dev|xyz|co|tv|site|online|info|biz)\b",
        r"t\.me(?:/|\b)", r"telegram\.me", r"telegram\.dog", r"tg://",
        r"youtube\.com", r"youtu\.be", r"youtube\.com/@",
        r"wa\.me", r"chat\.whatsapp\.com", r"whatsapp\.com",
        r"instagram\.com", r"facebook\.com", r"fb\.com", r"snapchat\.com",
        r"discord\.gg", r"discord\.com/invite", r"bit\.ly", r"tinyurl\.com",
        r"goo\.gl", r"cutt\.ly", r"rebrand\.ly",
        r"(ref=|referral|invite_code|promo[_-]?code|affiliate)",
    ]
    for p in patterns:
        if re.search(p, normal) or re.search(p, compact):
            return "link", "external link / referral"

    # External handles are promotion-prone when paired with platform/contact language
    handle = r"@[a-zA-Z0-9_][a-zA-Z0-9_]{4,}"
    promo_words = r"(join|channel|group|subscribe|follow|dm|inbox|contact|message|telegram|youtube|whatsapp|instagram)"
    if re.search(handle, text) and re.search(promo_words, normal):
        return "promotion", "external handle promotion"

    promotion_patterns = [
        r"join\s*(my|our)?\s*(channel|group)",
        r"(dm|inbox|message)\s*me",
        r"subscribe\s*(to|my)?", r"follow\s*(me|my)?",
        r"contact\s*(me|us)", r"paid\s*(service|promotion)",
        r"(buy|sell|selling)\s*(panel|service|account|promotion)",
        r"promotion\s*(available|service|offer)",
    ]
    for p in promotion_patterns:
        if re.search(p, normal) or re.search(re.sub(r"\\s\*", "", p), compact):
            return "promotion", "advertising / solicitation"

    # Basic multilingual abuse detection; compact scan catches spaced/symbol-separated bypasses
    abuse_words = [
        "madarchod", "maderchod", "mc", "bhenchod", "behenchod", "bc",
        "chutiya", "chutia", "gaand", "gandu", "randi", "harami",
        "fuck", "fucker", "motherfucker", "bitch", "asshole", "bastard",
    ]
    for word in abuse_words:
        if re.search(rf"\b{re.escape(word)}\b", normal) or word in compact:
            return "abuse", "abusive language"

    return None, ""


def contains_spam_or_link(text):
    return bool(detect_violation(text)[0])


def is_caps_spam(text):
    letters = [c for c in text if c.isalpha()]
    if len(letters) < CAPS_MIN_LETTERS:
        return False
    return sum(c.isupper() for c in letters) / len(letters) >= CAPS_RATIO


def is_mention_spam(text):
    return len(re.findall(r"(?<!\w)@[A-Za-z0-9_]{3,}", text or "")) > MENTION_LIMIT


def check_flood(chat_id, user_id, text):
    now = time.time()
    key = (chat_id, user_id)
    q = recent_messages[key]
    q.append(now)
    while q and now - q[0] > FLOOD_WINDOW:
        q.popleft()

    dq = duplicate_messages[key]
    normalized = compact_text(text)
    if normalized:
        dq.append((now, normalized))
    while dq and now - dq[0][0] > DUPLICATE_WINDOW:
        dq.popleft()

    duplicate_count = sum(1 for _, value in dq if value == normalized and normalized)
    if len(q) >= FLOOD_LIMIT:
        return "flood", "rapid message flood"
    if duplicate_count >= DUPLICATE_LIMIT:
        return "duplicate", "repeated duplicate spam"
    return None, ""


def is_admin_or_owner(chat_id, user_id):
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


def is_forwarded(message):
    return bool(getattr(message, "forward_date", None) or getattr(message, "forward_origin", None))


# ---------------- PUNISHMENT ----------------
def mute_user(chat_id, user_id, seconds=MUTE_SECONDS):
    try:
        until = int(time.time()) + seconds
        permissions = telebot.types.ChatPermissions(can_send_messages=False)
        bot.restrict_chat_member(chat_id, user_id, permissions=permissions, until_date=until)
        return True
    except Exception:
        return False


def handle_violation(message, reason):
    chat_id = message.chat.id
    user_id = message.from_user.id
    track_message(message, suspicious=True, reason=reason)

    try:
        bot.delete_message(chat_id, message.message_id)
    except Exception:
        pass

    strikes = add_strike(chat_id, user_id, reason)

    if strikes >= 5:
        delete_tracked_violations(chat_id, user_id)
        try:
            bot.ban_chat_member(chat_id, user_id)
            bot.send_message(chat_id, f"🚨 User banned automatically after {strikes} strikes. Reason: {reason}")
        except Exception:
            pass
    elif strikes >= 3:
        delete_tracked_violations(chat_id, user_id)
        mute_user(chat_id, user_id)
        try:
            bot.send_message(chat_id, f"⚠️ {strikes} strikes: user auto-muted for repeated violations.")
        except Exception:
            pass
    else:
        try:
            bot.send_message(chat_id, f"⚠️ Warning {strikes}/5: {reason}")
        except Exception:
            pass


# ---------------- COMMANDS ----------------
@bot.message_handler(commands=["start"])
def start_handler(message):
    user_id = message.from_user.id
    get_user_data(user_id)
    if message.chat.type in ("group", "supergroup"):
        bot.reply_to(message, "Heyy ✨ Main Maya hoon! Is group mein advanced anti-spam aur security active hai 😎🛡️ Admin `/chaton` use karke group AI chat enable kar sakte hain.")
    else:
        bot.reply_to(message, "Heyy ✨ 🌸 Main Maya hoon 🌻\nAap batao aapka kya naam h? ✨ Kuch baatein karein?")


@bot.message_handler(commands=["chaton"])
def enable_group_chat(message):
    if message.chat.type in ("group", "supergroup"):
        if is_admin_or_owner(message.chat.id, message.from_user.id):
            active_group_chats.add(message.chat.id)
            bot.reply_to(message, "✨ Group chat enabled! Ab aap sabhi mujhse baatein kar sakte hain 😎🌻")
        else:
            bot.reply_to(message, "⚠️ Sirf Admin ya Owner hi group chat enable kar sakte hain!")


@bot.message_handler(commands=["chatoff"])
def disable_group_chat(message):
    if message.chat.type in ("group", "supergroup"):
        if is_admin_or_owner(message.chat.id, message.from_user.id):
            active_group_chats.discard(message.chat.id)
            bot.reply_to(message, "🔒 Group chat disabled. Ab main security mode par focus karungi! 🛡️")
        else:
            bot.reply_to(message, "⚠️ Sirf Admin ya Owner hi ye command de sakte hain!")


@bot.message_handler(commands=["lock"])
def lock_handler(message):
    if message.chat.type not in ("group", "supergroup") or not is_admin_or_owner(message.chat.id, message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or parts[1].lower() not in ("media", "forwards"):
        bot.reply_to(message, "Usage: `/lock media` or `/lock forwards`", parse_mode="Markdown")
        return
    key = "lock_media" if parts[1].lower() == "media" else "lock_forwards"
    set_group_setting(message.chat.id, key, True)
    bot.reply_to(message, f"🔒 {parts[1].lower()} lock enabled.")


@bot.message_handler(commands=["unlock"])
def unlock_handler(message):
    if message.chat.type not in ("group", "supergroup") or not is_admin_or_owner(message.chat.id, message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or parts[1].lower() not in ("media", "forwards"):
        bot.reply_to(message, "Usage: `/unlock media` or `/unlock forwards`", parse_mode="Markdown")
        return
    key = "lock_media" if parts[1].lower() == "media" else "lock_forwards"
    set_group_setting(message.chat.id, key, False)
    bot.reply_to(message, f"🔓 {parts[1].lower()} lock disabled.")


@bot.message_handler(commands=["help"])
def help_handler(message):
    help_text = (
        "🛡️ **Maya Advanced Security & Control Panel**\n\n"
        "💬 `/chaton` - Enable AI group chat\n"
        "🔒 `/chatoff` - Disable AI group chat\n"
        "🚫 `/ban` - Reply to user and ban + cleanup\n"
        "👢 `/kick` - Reply to user and kick + cleanup\n"
        "🧹 `/delete` - Reply to user and cleanup tracked suspicious history\n"
        "🔐 `/lock media` | `/unlock media`\n"
        "📨 `/lock forwards` | `/unlock forwards`\n"
        "🆔 `/id` - User and chat IDs\n\n"
        "Auto: links, promotions, abuse, flood, duplicates, caps, mention spam, persistent strikes, progressive mute/ban, bot-add protection and leave cleanup."
    )
    bot.reply_to(message, help_text, parse_mode="Markdown")


@bot.message_handler(commands=["id"])
def get_id_handler(message):
    bot.reply_to(message, f"👤 User ID: `{message.from_user.id}`\n👥 Chat ID: `{message.chat.id}`", parse_mode="Markdown")


def get_reply_target(message):
    return message.reply_to_message.from_user if message.reply_to_message and message.reply_to_message.from_user else None


@bot.message_handler(commands=["ban"])
def ban_user(message):
    if message.chat.type not in ("group", "supergroup"):
        return
    try:
        target = get_reply_target(message)
        if is_admin_or_owner(message.chat.id, message.from_user.id) and target:
            delete_tracked_violations(message.chat.id, target.id)
            bot.ban_chat_member(message.chat.id, target.id)
            bot.reply_to(message, "🚫 User banned and tracked suspicious history cleanup attempted! 🧹")
        else:
            bot.reply_to(message, "⚠️ Admin rights chahiye aur user ke message par reply karo!")
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {str(e)}")


@bot.message_handler(commands=["kick"])
def kick_user(message):
    if message.chat.type not in ("group", "supergroup"):
        return
    try:
        target = get_reply_target(message)
        if is_admin_or_owner(message.chat.id, message.from_user.id) and target:
            delete_tracked_violations(message.chat.id, target.id)
            bot.ban_chat_member(message.chat.id, target.id)
            bot.unban_chat_member(message.chat.id, target.id)
            bot.reply_to(message, "👢 User kicked and tracked suspicious history cleanup attempted! 🧹")
        else:
            bot.reply_to(message, "⚠️ Admin rights chahiye aur message par reply karo!")
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {str(e)}")


@bot.message_handler(commands=["delete"])
def delete_user_history(message):
    if message.chat.type not in ("group", "supergroup"):
        return
    if not is_admin_or_owner(message.chat.id, message.from_user.id):
        bot.reply_to(message, "⚠️ Sirf Admin/Owner ye cleanup command use kar sakte hain.")
        return
    target = get_reply_target(message)
    if not target:
        bot.reply_to(message, "Reply to a user's message with `/delete`.", parse_mode="Markdown")
        return
    deleted = delete_tracked_violations(message.chat.id, target.id, force_rescan=True)
    try:
        bot.delete_message(message.chat.id, message.reply_to_message.message_id)
    except Exception:
        pass
    bot.reply_to(message, f"🧹 Cleanup complete: {deleted} tracked suspicious message(s) removed.")


# ---------------- MEMBER JOIN / BOT ADD ----------------
@bot.message_handler(content_types=["new_chat_members"])
def welcome_new_members(message):
    chat_name = message.chat.title or "this group"
    for member in message.new_chat_members:
        if member.id == bot.get_me().id:
            bot.reply_to(message, f"Hii everyone! ✨ Main Maya hoon. **{chat_name}** me advanced security active hai 😎🛡️", parse_mode="Markdown")
            continue

        # Unauthorized bot add protection: remove newly added bots when possible
        if getattr(member, "is_bot", False):
            try:
                bot.ban_chat_member(message.chat.id, member.id)
                bot.unban_chat_member(message.chat.id, member.id)
                bot.reply_to(message, "🤖 Unauthorized bot removed by security protection.")
            except Exception:
                pass
            continue

        username = f"@{member.username}" if member.username else member.first_name
        bot.reply_to(
            message,
            f"👋 Hey {username}!\n✨ Welcome to **{chat_name}** 🌻\n🛡️ Links, promotions aur spam restricted hain.",
            parse_mode="Markdown",
        )


# ---------------- LEAVE CLEANUP ----------------
@bot.message_handler(content_types=["left_chat_member"])
def member_left_handler(message):
    member = getattr(message, "left_chat_member", None)
    if member and message.chat.type in ("group", "supergroup"):
        deleted = delete_tracked_violations(message.chat.id, member.id, force_rescan=True)
        if deleted:
            try:
                bot.send_message(message.chat.id, f"🧹 Leaving user ki {deleted} tracked suspicious old message(s) cleanup ki gayi.")
            except Exception:
                pass


# ---------------- MAIN MESSAGE HANDLER ----------------
@bot.message_handler(
    func=lambda message: True,
    content_types=["text", "photo", "video", "document", "audio", "voice", "sticker", "animation"],
)
def main_message_handler(message):
    chat_type = message.chat.type
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else None

    if chat_type in ("group", "supergroup"):
        privileged = is_admin_or_owner(chat_id, user_id)
        settings = get_group_settings(chat_id)
        text_content = message.text or message.caption or ""

        # Track first so retroactive cleanup can work even if future rules improve.
        track_message(message, suspicious=False)

        if not privileged:
            if settings["lock_media"] and message.content_type in ("photo", "video", "document", "audio", "voice", "sticker", "animation"):
                handle_violation(message, "media is locked")
                return

            if settings["lock_forwards"] and is_forwarded(message):
                handle_violation(message, "forwarded messages are locked")
                return

            reason_type, reason = detect_violation(text_content)
            if reason_type:
                handle_violation(message, reason)
                return

            flood_type, flood_reason = check_flood(chat_id, user_id, text_content)
            if flood_type:
                handle_violation(message, flood_reason)
                return

            if is_caps_spam(text_content):
                handle_violation(message, "excessive capital letters")
                return

            if is_mention_spam(text_content):
                handle_violation(message, "too many mentions")
                return

            # Existing behaviour: forwarded content is treated as suspicious protection event.
            if is_forwarded(message):
                handle_violation(message, "forwarded content")
                return

        if chat_id not in active_group_chats:
            return

    # Private inbox or enabled group AI chat
    user_text = message.text or message.caption
    if not user_text:
        return

    history, rel_level, current_emotion = get_user_data(user_id)

    try:
        bot.send_chat_action(chat_id, "typing")
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": MODEL_ID,
            "messages": [
                {"role": "system", "content": f"{BASE_SYSTEM_PROMPT}\n[Context: Level {rel_level}]\nMemory:\n{history}"},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.85,
            "max_tokens": 300,
        }

        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=20,
        )
        res_json = response.json()

        if "choices" in res_json and res_json["choices"]:
            ai_reply = res_json["choices"][0]["message"]["content"].strip()
        else:
            ai_reply = "Acha ji! 😅"

        if not ai_reply:
            ai_reply = "Hmm..."

        updated_history = history + f"\nUser: {user_text}\nMaya: {ai_reply}"
        if len(updated_history) > 2000:
            updated_history = updated_history[-2000:]

        update_user_data(user_id, updated_history, rel_level, current_emotion)
        bot.reply_to(message, ai_reply)

    except Exception as e:
        print(f"AI Error: {str(e)}")


# ---------------- FLASK HOST ----------------
app = Flask(__name__)


@app.route("/")
def home():
    return "✨ Maya AI Companion & Advanced Security Bot is Live!"


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    print("✨ Maya Advanced Security & AI Bot is running...")
    bot.infinity_polling(skip_pending=True)
