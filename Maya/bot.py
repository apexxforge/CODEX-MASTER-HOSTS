import os
import threading
import sqlite3
import re
import time
import unicodedata
import random
import json
import html
from collections import defaultdict, deque

import requests
import telebot
from flask import Flask

TELEGRAM_BOT_TOKEN = "8975065411:AAE8wUwhTBEUsq_Mxj2n8XWHHBCtRpodUYA"

GROQ_API_KEY = "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"

MODEL_ID = "openai/gpt-oss-20b"

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
active_group_chats = set()
BOT_PROFILE_CACHE = {"id": None, "name": "AI", "username": "", "fetched_at": 0}
BOT_IDENTITY_REFRESH_SECONDS = 60
AI_CONVERSATION_TIMEOUT = 5 * 60
COMMAND_COOLDOWN_SECONDS = 8
command_last_used = {}

# Tunable moderation thresholds
FLOOD_WINDOW = 10
FLOOD_LIMIT = 6
DUPLICATE_WINDOW = 60
DUPLICATE_LIMIT = 3
CAPS_MIN_LETTERS = 12
CAPS_RATIO = 0.80
MENTION_LIMIT = 5
MUTE_SECONDS = 10 * 60
MAX_TRACKED_MESSAGES_PER_USER = 500
WARNING_DELETE_SECONDS = 20
ACTION_DELETE_SECONDS = 20
WELCOME_DELETE_SECONDS = 60

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
            CREATE TABLE IF NOT EXISTS user_profiles (
                chat_id INTEGER,
                user_id INTEGER,
                language TEXT DEFAULT 'auto',
                style TEXT DEFAULT 'casual',
                avg_length REAL DEFAULT 0,
                samples INTEGER DEFAULT 0,
                last_seen INTEGER,
                PRIMARY KEY(chat_id, user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS group_context (
                chat_id INTEGER PRIMARY KEY,
                recent_context TEXT DEFAULT '[]',
                last_bot_reply INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS ai_conversation_sessions (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                last_message_at INTEGER NOT NULL,
                PRIMARY KEY(chat_id, user_id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_ai_session_expiry ON ai_conversation_sessions(expires_at)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS group_settings (
                chat_id INTEGER PRIMARY KEY,
                lock_media INTEGER DEFAULT 0,
                lock_forwards INTEGER DEFAULT 0,
                ai_enabled INTEGER DEFAULT 0
            )
        """)
        # Migration for older databases
        try: c.execute("ALTER TABLE group_settings ADD COLUMN ai_enabled INTEGER DEFAULT 0")
        except sqlite3.OperationalError: pass
        c.execute("""CREATE TABLE IF NOT EXISTS link_permissions (
            chat_id INTEGER, user_id INTEGER, expires_at INTEGER, granted_by INTEGER,
            PRIMARY KEY(chat_id, user_id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS trusted_users (
            chat_id INTEGER, user_id INTEGER, granted_by INTEGER, created_at INTEGER,
            PRIMARY KEY(chat_id, user_id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS bot_add_permissions (
            chat_id INTEGER, user_id INTEGER, expires_at INTEGER, granted_by INTEGER,
            PRIMARY KEY(chat_id, user_id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS cleanup_jobs (
            chat_id INTEGER, message_id INTEGER, run_at INTEGER, attempts INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id, message_id)
        )""")
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
        c.execute("SELECT lock_media, lock_forwards, ai_enabled FROM group_settings WHERE chat_id = ?", (chat_id,))
        row = c.fetchone()
        if not row:
            c.execute("INSERT OR IGNORE INTO group_settings(chat_id, ai_enabled) VALUES (?, 0)", (chat_id,))
            conn.commit()
            return {"lock_media": 0, "lock_forwards": 0, "ai_enabled": 0}
        return {"lock_media": row[0], "lock_forwards": row[1], "ai_enabled": row[2]}

def set_group_setting(chat_id, key, value):
    if key not in ("lock_media", "lock_forwards", "ai_enabled"):
        return
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        conn.execute("INSERT OR IGNORE INTO group_settings(chat_id) VALUES (?)", (chat_id,))
        conn.execute(f"UPDATE group_settings SET {key} = ? WHERE chat_id = ?", (1 if value else 0, chat_id))
        conn.commit()
    if key == "ai_enabled":
        (active_group_chats.add if value else active_group_chats.discard)(chat_id)

def has_link_permission(chat_id, user_id):
    now = int(time.time())
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        row = conn.execute("SELECT expires_at FROM link_permissions WHERE chat_id=? AND user_id=?", (chat_id,user_id)).fetchone()
        if not row: return False
        exp = row[0]
        if exp and exp < now:
            conn.execute("DELETE FROM link_permissions WHERE chat_id=? AND user_id=?", (chat_id,user_id)); conn.commit(); return False
        return True

def has_bot_add_permission(chat_id, user_id):
    now = int(time.time())
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        row = conn.execute("SELECT expires_at FROM bot_add_permissions WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
        if not row:
            return False
        exp = row[0]
        if exp and exp < now:
            conn.execute("DELETE FROM bot_add_permissions WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            conn.commit()
            return False
        return True

def is_trusted(chat_id, user_id):
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        return bool(conn.execute("SELECT 1 FROM trusted_users WHERE chat_id=? AND user_id=?", (chat_id,user_id)).fetchone())

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
            """INSERT INTO tracked_messages
               (chat_id, user_id, message_id, content, content_type, suspicious, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, message_id) DO UPDATE SET
               suspicious=MAX(tracked_messages.suspicious, excluded.suspicious),
               reason=CASE WHEN excluded.reason != '' THEN excluded.reason ELSE tracked_messages.reason END""",
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
            reason_type, _ = detect_violation(content)
            suspicious = suspicious or bool(reason_type)
            # Deep fallback for messages that contain domain-like or promotion-like structures.
            normalized = normalize_text(content)
            deep_patterns = (
                r"\b[a-z0-9-]{2,}\.(com|net|org|in|io|me|gg|ly|app|xyz|site|link)\b",
                r"(?:join|subscribe|follow|contact|dm|inbox|message)\s*(?:me|us)?",
                r"(?:telegram|youtube|whatsapp|instagram|facebook|snapchat|discord|channel|group)",
                r"@[a-z0-9_]{4,}",
            )
            suspicious = suspicious or any(re.search(p, normalized, re.I) for p in deep_patterns)
        if suspicious and delete_message_safe(chat_id, message_id):
            deleted += 1
    return deleted


def delete_all_tracked_messages(chat_id, user_id):
    """Best-effort cleanup of every message the bot has tracked for a user."""
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        rows = conn.execute(
            "SELECT message_id FROM tracked_messages WHERE chat_id=? AND user_id=? ORDER BY created_at DESC",
            (chat_id, user_id),
        ).fetchall()
    deleted = 0
    for (message_id,) in rows:
        if delete_message_safe(chat_id, message_id, retries=3):
            deleted += 1
    return deleted


# ---------------- NORMALIZATION + DETECTION ----------------
BASE_SYSTEM_PROMPT = """
You are a believable, warm and socially intelligent young Indian female chat companion. Your public Telegram identity is supplied dynamically with every request and is authoritative.

IDENTITY RULES:
- Your name is ALWAYS the current Telegram DISPLAY NAME supplied in the runtime identity block. The @username is only a handle and may be completely different.
- If asked "tera naam kya hai?", "what is your name?", or similar, answer with the current display name exactly. Never invent another personal name.
- A creator/project/organization name is NOT your personal name. Never confuse a creator, brand, server, project or developer name with your own name.
- Never hardcode or assume names such as Maya, Miya, Alya, Apex, Apexia or any other fixed name; trust only the runtime identity block.

NATURAL CHAT STYLE:
- Do not repeatedly introduce yourself as an AI, robot, chatbot, language model, virtual assistant, program or automated system.
- Do not volunteer technical self-descriptions in ordinary conversation. Talk naturally and directly, like a normal participant with a consistent personality.
- Only explain that you are a bot/digital assistant if the user explicitly asks what you are; even then answer briefly and naturally, without repeating disclaimers.
- Never announce that you are following rules and never sound like a customer-support bot.
- Adapt naturally to Hindi, English, Hinglish, Roman Urdu, and Indian regional-language words. Understand typos, abbreviations, broken Hindi-English and informal texting.
- Mirror the user's approximate style without caricaturing it. If they write "ky kr rhi", understand it naturally.
- Vary response length: greetings/reactions are short; emotional conversations are thoughtful; technical questions are structured and detailed.
- Do not use the same emoji, catchphrase or sentence pattern repeatedly. Emojis are optional, not mandatory.
- Read emotion and intent: joking, sarcasm, anger, sadness, confusion, excitement, frustration, flirting and seriousness.
- In groups, do NOT hijack human conversations. Reply only when directly addressed, mentioned, replying to you, or continuing an active per-user conversation session.
- When multiple people are talking, identify who is speaking from the supplied context and never pretend a message was addressed to you when it was not.
- Sound like one consistent person, but let tone vary naturally with familiarity and context.
- In PRIVATE CHAT, every normal user text message is a direct conversation with you. Answer it directly and contextually; do not require a mention, trigger word, or repeated activation.
- Never answer a meaningful question with only "Hmm", "Hmm...", "Acha ji" or another empty filler. Give an actual answer.
"""



def fallback_ai_reply(user_text, display_name):
    """Natural fallback used only when the AI provider temporarily returns no usable text."""
    q = (user_text or "").strip().lower()
    if re.search(r"(tera|tra|tr|tumhara|tumara|tuhara|aapka|apka|your)\s*(naam|nam|name)", q):
        return f"Mera naam {display_name} hai 😊"
    if any(x in q for x in ("kisne banaya", "who made you", "developer kon", "developer kaun", "creator kon", "creator kaun")):
        return "Mujhe Apex X Forge ne build kiya hai."
    if any(x in q for x in ("hello", "hii", "hi ", "hey")):
        return "Heyy 😊 bolo, kya baat hai?"
    return "Ek sec, message samajh rahi hu 😅 tum apni baat ek baar aur bhejo na."


def request_ai_completion(headers, payload, fallback_text):
    """Retry provider calls and return a clean reply instead of a meaningless one-word fallback."""
    last_error = None
    for attempt in range(2):
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            if response.status_code >= 400:
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                time.sleep(0.8 * (attempt + 1))
                continue
            data = response.json()
            choices = data.get("choices") or []
            if choices:
                content = ((choices[0].get("message") or {}).get("content") or "").strip()
                if content and content.lower() not in {"hmm", "hmm...", "acha ji", "acha ji! 😅"}:
                    return content
            last_error = str(data.get("error") or "Provider returned an empty completion")
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.8 * (attempt + 1))
    print(f"AI completion fallback: {last_error}")
    return fallback_text

def analyze_style(text):
    t = text or ""
    low = t.lower()
    if re.search(r"[ऀ-ॿ]", t): lang = "Hindi"
    elif any(x in low.split() for x in ("kya","ky","hai","h","bhai","acha","haan","nhi","nahi","kr","rha","rhi")): lang = "Hinglish"
    elif re.search(r"[a-zA-Z]", t): lang = "English/Roman"
    else: lang = "mixed"
    style = "short" if len(t) < 35 else "detailed" if len(t) > 180 else "casual"
    return lang, style


def update_social_context(message):
    if message.chat.type not in ("group", "supergroup") or not message.from_user or getattr(message.from_user, "is_bot", False):
        return
    chat_id, user_id = message.chat.id, message.from_user.id
    text = message.text or message.caption or ""
    lang, style = analyze_style(text)
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        conn.execute("""INSERT INTO user_profiles(chat_id,user_id,language,style,avg_length,samples,last_seen)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET
        language=excluded.language, style=excluded.style,
        avg_length=((user_profiles.avg_length*user_profiles.samples)+excluded.avg_length)/(user_profiles.samples+1),
        samples=user_profiles.samples+1,last_seen=excluded.last_seen""",
        (chat_id,user_id,lang,style,len(text),1,int(time.time())))
        row=conn.execute("SELECT recent_context FROM group_context WHERE chat_id=?",(chat_id,)).fetchone()
        try: ctx=json.loads(row[0]) if row else []
        except Exception: ctx=[]
        ctx.append({"user_id":user_id,"name":message.from_user.first_name or "User","text":text[-500:]})
        ctx=ctx[-18:]
        conn.execute("INSERT INTO group_context(chat_id,recent_context,last_bot_reply) VALUES(?,?,0) ON CONFLICT(chat_id) DO UPDATE SET recent_context=excluded.recent_context",(chat_id,json.dumps(ctx,ensure_ascii=False)))
        conn.commit()


def get_group_context(chat_id):
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        row=conn.execute("SELECT recent_context,last_bot_reply FROM group_context WHERE chat_id=?",(chat_id,)).fetchone()
    if not row: return [],0
    try: return json.loads(row[0]),row[1]
    except Exception: return [],row[1]


def should_ai_reply_in_group(message, text):
    me = bot.get_me(); low=normalize_text(text or "")
    direct_mention=bool(me.username and ("@"+me.username.lower()) in low)
    reply_to_bot=bool(message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id==me.id)
    name_addressed=natural_name_addressed(text)
    recent,last=get_group_context(message.chat.id)
    if direct_mention or reply_to_bot or name_addressed: return True
    # Generic questions are answered only when context strongly points to the bot.
    if time.time()-last<35: return False
    if "?" not in (text or ""): return False
    recent_users={x.get("user_id") for x in recent[-6:]}
    return len(recent_users)<=2 and bool(re.search(r"\b(ai|bot)\b",low,re.I))



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
    # Common visual obfuscation used in spaced/broken links
    text = re.sub(r"\s*\.\s*", ".", text)
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s*:\s*", ":", text)
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
        r"goo\.gl", r"cutt\.ly", r"rebrand\.ly", r"shorturl\.at", r"is\.gd", r"t\.co",
        r"tiktok\.com", r"x\.com", r"twitter\.com", r"linkedin\.com", r"threads\.net",
        r"reddit\.com", r"pinterest\.com", r"linktr\.ee", r"beacons\.ai",
        r"(ref=|referral|invite_code|promo[_-]?code|affiliate)",
    ]
    for p in patterns:
        if re.search(p, normal) or re.search(p, compact):
            return "link", "external link / referral"

    # Compact scan catches examples like: t e l e g r a m . m e / x or y o u t u . b e / x
    compact_domains = ("telegram", "tme", "youtube", "youtu", "whatsapp", "instagram", "facebook", "snapchat", "discord", "tiktok", "linktree")
    if any(d in compact for d in compact_domains) and any(x in compact for x in (".", "/", "com", "me", "be", "gg", "join", "channel", "group")):
        return "link", "obfuscated platform link/promotion"

    # External handles are promotion-prone when paired with platform/contact language
    handle = r"@[a-zA-Z0-9_][a-zA-Z0-9_]{4,}"
    promo_words = r"(join|channel|group|subscribe|follow|dm|inbox|contact|message|telegram|youtube|whatsapp|instagram)"
    if re.search(handle, normal) and re.search(promo_words, normal):
        return "promotion", "external handle promotion"
    # Telegram/channel/profile style @handles and invite language
    if re.search(r"(?:join|joinnow|subscribe|follow|dm|inbox|contact|msg|message).{0,40}@", compact):
        return "promotion", "contact/handle promotion"

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


def bot_can_delete(chat_id):
    try:
        me = bot.get_me()
        member = bot.get_chat_member(chat_id, me.id)
        return member.status in ("creator", "administrator") and bool(getattr(member, "can_delete_messages", False) or member.status == "creator")
    except Exception:
        return False


# ---------------- RELIABLE AUTO-CLEANUP ----------------
AUTO_DELETE_NOTICES_SECONDS = ACTION_DELETE_SECONDS

def queue_cleanup(chat_id, message_id, seconds):
    run_at = int(time.time() + max(1, seconds))
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        conn.execute("INSERT OR REPLACE INTO cleanup_jobs(chat_id,message_id,run_at,attempts) VALUES(?,?,?,0)", (chat_id,message_id,run_at)); conn.commit()

def delete_later(chat_id, message_id, seconds=AUTO_DELETE_NOTICES_SECONDS):
    queue_cleanup(chat_id, message_id, seconds)

def cleanup_worker():
    while True:
        try:
            now=int(time.time())
            with sqlite3.connect("maya_memory.db", timeout=10) as conn:
                jobs=conn.execute("SELECT chat_id,message_id,attempts FROM cleanup_jobs WHERE run_at<=? LIMIT 50", (now,)).fetchall()
            for chat_id,message_id,attempts in jobs:
                ok=delete_message_safe(chat_id,message_id,retries=2)
                with sqlite3.connect("maya_memory.db", timeout=10) as conn:
                    if ok or attempts>=8:
                        conn.execute("DELETE FROM cleanup_jobs WHERE chat_id=? AND message_id=?",(chat_id,message_id))
                    else:
                        delay=min(300, 5*(2**attempts))
                        conn.execute("UPDATE cleanup_jobs SET attempts=?,run_at=? WHERE chat_id=? AND message_id=?",(attempts+1,int(time.time()+delay),chat_id,message_id))
                    conn.commit()
        except Exception as e: print("CLEANUP WORKER:",e)
        time.sleep(2)

def send_temporary(chat_id, text, seconds=AUTO_DELETE_NOTICES_SECONDS, **kwargs):
    try:
        sent = bot.send_message(chat_id, text, **kwargs)
        delete_later(chat_id, sent.message_id, seconds)
        return sent
    except Exception as e:
        print("SEND TEMP ERROR:",e); return None

def delete_message_safe(chat_id, message_id, retries=4):
    for attempt in range(retries):
        try:
            bot.delete_message(chat_id, message_id); return True
        except Exception as e:
            print(f"DELETE ERROR attempt={attempt+1} chat={chat_id} msg={message_id}: {e}")
            time.sleep(min(2.5, 0.4*(attempt+1)))
    return False

def mention_html(user):
    name=html.escape(user.first_name or user.username or "User")
    return f'<a href="tg://user?id={user.id}">{name}</a>'

def human_admin_mentions(chat_id):
    humans=[]
    try:
        for m in bot.get_chat_administrators(chat_id):
            u=getattr(m,"user",None)
            if not u or getattr(u,"is_bot",False): continue
            if u.id == bot.get_me().id: continue
            humans.append((m,u))
    except Exception as e: print("ADMIN FETCH:",e)
    owner=[u for m,u in humans if getattr(m,"status","") in ("creator","owner")]
    admins=[u for m,u in humans if u not in owner]
    owner_text=" • ".join(mention_html(u) for u in owner) or "Owner"
    admin_text=" • ".join(mention_html(u) for u in admins) or "No additional human admins"
    return owner_text, admin_text

def warning_text(chat_id, user, strikes, reason):
    owner_text, admin_text = human_admin_mentions(chat_id)
    return (f"⚠️ <b>Warning {strikes}/5</b> — {mention_html(user)}\n\n"
            "Is group me 🔗 kisi bhi type ke <b>Link, Promotion, Spam ya Abusing</b> allowed nahi hai.\n\n"
            "Agar aapko links share karne hain to phle Owner ya Admins se permission lein:\n"
            f"👑 <b>Owner:</b> {owner_text}\n"
            f"🛡️ <b>Admins:</b> {admin_text}\n\n"
            "🗑️ <b>Your message has been removed.</b>\n"
            "Please don't share links or promotions here.")

def command_is_spam(chat_id,user_id,command):
    key=(chat_id,user_id,command); now=time.time(); last=command_last_used.get(key,0); command_last_used[key]=now
    return now-last<COMMAND_COOLDOWN_SECONDS

def get_bot_identity(force=False):
    """Fetch the current Telegram identity without hardcoding a bot name.
    The visible display name and @username are intentionally treated as two
    separate triggers because Telegram users normally address a bot by name.
    """
    now = time.time()
    if force or BOT_PROFILE_CACHE["id"] is None or now - BOT_PROFILE_CACHE.get("fetched_at", 0) >= BOT_IDENTITY_REFRESH_SECONDS:
        try:
            me = bot.get_me()
            BOT_PROFILE_CACHE.update({
                "id": me.id,
                "name": (me.first_name or "AI").strip(),
                "username": (me.username or "").lower().strip(),
                "fetched_at": now,
            })
        except Exception:
            pass
    return BOT_PROFILE_CACHE


def natural_name_addressed(text):
    """Detect the bot's CURRENT Telegram display name as a natural address.
    Examples: 'Miya kesi ho', 'Hi Miya', 'oye Miya suno'.
    It does not depend on @username and supports future name changes.
    """
    ident = get_bot_identity()
    name = normalize_text(ident.get("name") or "").strip()
    low = normalize_text(text or "").strip()
    if not name or not low or len(name) < 2:
        return False

    esc = re.escape(name)
    boundary = rf"(?<![\w]){esc}(?![\w])"
    # Strong natural addressing patterns: name at start/end, greeting + name,
    # or name followed by common conversational words/punctuation.
    patterns = (
        rf"^(?:oye|hey|hi|hello|arey|arre|suno)?\s*[,!:.\-]*\s*{boundary}",
        rf"{boundary}\s*[,!?.:]*\s*(?:sun|suno|tum|tu|aap|kya|ky|kaise|kesi|kesa|kaha|kidhar|idr|idhar|help|please|bhai|ho|hai|h|batao|bolo)",
        rf"(?:^|[,!?.:]\s*)(?:hi|hey|hello|oye|arey|arre)\s+{boundary}",
        rf"{boundary}\s*[?!]",
    )
    return any(re.search(pattern, low, re.I) for pattern in patterns)


def activate_ai_conversation(chat_id, user_id):
    now = int(time.time())
    expires = now + AI_CONVERSATION_TIMEOUT
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        conn.execute(
            "INSERT INTO ai_conversation_sessions(chat_id,user_id,expires_at,last_message_at) VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET expires_at=excluded.expires_at,last_message_at=excluded.last_message_at",
            (chat_id, user_id, expires, now),
        )
        conn.execute("DELETE FROM ai_conversation_sessions WHERE expires_at < ?", (now,))
        conn.commit()


def has_active_ai_conversation(chat_id, user_id):
    now = int(time.time())
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        row = conn.execute(
            "SELECT expires_at FROM ai_conversation_sessions WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
    return bool(row and int(row[0]) >= now)


def should_ai_reply_in_group(message, text):
    # Never let the AI talk to another Telegram bot or enter bot reply loops.
    if not message.from_user or getattr(message.from_user, "is_bot", False):
        return False

    ident = get_bot_identity()
    low = normalize_text(text or "")
    username = ident.get("username") or ""
    direct_mention = bool(username and ("@" + username) in low)
    reply_to_bot = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == ident.get("id")
    )
    name_addressed = natural_name_addressed(text)

    # A direct address starts/refreshes a conversation for THIS user only.
    if direct_mention or reply_to_bot or name_addressed:
        activate_ai_conversation(message.chat.id, message.from_user.id)
        return True

    # Follow-up conversation: only the same user gets a short-lived session.
    # Other group members are ignored unless they directly address the bot.
    if has_active_ai_conversation(message.chat.id, message.from_user.id):
        clean = (text or "").strip()
        if clean and not clean.startswith("/"):
            # If explicitly talking to another @user, don't hijack it.
            if not re.match(r"^@[A-Za-z0-9_]{4,}\b", clean):
                activate_ai_conversation(message.chat.id, message.from_user.id)
                return True

    return False


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
    chat_id = message.chat.id; user_id = message.from_user.id
    track_message(message, suspicious=True, reason=reason)
    delete_message_safe(chat_id, message.message_id, retries=6)
    strikes = add_strike(chat_id, user_id, reason)
    if strikes >= 5:
        deleted=delete_tracked_violations(chat_id,user_id,True)
        try: bot.ban_chat_member(chat_id,user_id)
        except Exception as e: print("BAN ERROR:",e)
        send_temporary(chat_id, f"🚨 <b>User Banned</b>\n\n{mention_html(message.from_user)} has received <b>{strikes} warnings</b>.\n🔨 Repeated violations detected.\n🧹 {deleted} tracked suspicious messages cleaned.", ACTION_DELETE_SECONDS, parse_mode="HTML")
    elif strikes >= 3:
        deleted=delete_tracked_violations(chat_id,user_id,True)
        muted=mute_user(chat_id,user_id)
        send_temporary(chat_id, f"🔇 <b>User Muted</b>\n\n{mention_html(message.from_user)} has received <b>{strikes} warnings</b>.\n⏳ Temporarily muted for repeated violations.\n🧹 {deleted} suspicious messages cleaned.", ACTION_DELETE_SECONDS, parse_mode="HTML")
    else:
        send_temporary(chat_id, warning_text(chat_id,message.from_user,strikes,reason), WARNING_DELETE_SECONDS, parse_mode="HTML", disable_web_page_preview=True)


# ---------------- FORCE CHANNEL JOIN ----------------
CHANNELS_FILE = "channels.txt"
REQUIRED_MEMBER_STATUSES = {"member", "administrator", "creator", "owner", "restricted"}


def load_required_channels():
    """Dynamically read one public Telegram channel per line from channels.txt."""
    channels = []
    try:
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                clean = line.rstrip("/")
                handle = clean
                if "t.me/" in clean:
                    handle = clean.split("t.me/", 1)[1].split("?", 1)[0].strip("/")
                if handle.startswith("@"):
                    chat_ref, url, label = handle, f"https://t.me/{handle[1:]}", handle
                elif re.fullmatch(r"-?\d+", handle):
                    chat_ref, url, label = int(handle), None, str(handle)
                else:
                    chat_ref, url, label = "@" + handle, f"https://t.me/{handle}", "@" + handle
                channels.append({"chat": chat_ref, "url": url, "label": label})
    except FileNotFoundError:
        pass
    return channels


def is_joined_to_required_channels(user_id):
    missing = []
    for channel in load_required_channels():
        try:
            member = bot.get_chat_member(channel["chat"], user_id)
            status = str(getattr(member, "status", "")).lower()
            if status not in REQUIRED_MEMBER_STATUSES:
                missing.append(channel)
        except Exception as e:
            print("CHANNEL VERIFY ERROR:", channel["label"], e)
            missing.append(channel)
    return len(missing) == 0, missing


def build_join_markup(channels):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    for i, channel in enumerate(channels, 1):
        if channel.get("url"):
            markup.add(telebot.types.InlineKeyboardButton(f"📢 Join Channel {i}", url=channel["url"]))
    markup.add(telebot.types.InlineKeyboardButton("✅ Verify Membership", callback_data="verify_required_channels"))
    return markup


def send_join_required_ui(message, edit=False, missing=None):
    channels = missing if missing is not None else load_required_channels()
    if not channels:
        return None
    text = ("🔐 <b>Access Verification Required</b>\n\n"
            "Heyy 👋 Bot use karne se pehle hamare official channels join karna zaroori hai.\n\n"
            "📢 Neeche diye gaye <b>saare channels</b> join karo, phir <b>Verify Membership</b> dabao.\n\n"
            "✨ Join complete hone ke baad verification automatically check ho jayega.")
    markup = build_join_markup(channels)
    if edit:
        try:
            bot.edit_message_text(text, message.chat.id, message.message_id, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
            return
        except Exception:
            pass
    return bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)


def private_access_allowed(message):
    if message.chat.type != "private":
        return True
    channels = load_required_channels()
    if not channels:
        return True
    ok, missing = is_joined_to_required_channels(message.from_user.id)
    if ok:
        return True
    send_join_required_ui(message, missing=missing)
    return False


@bot.callback_query_handler(func=lambda call: call.data == "verify_required_channels")
def verify_required_channels(call):
    if call.message.chat.type != "private":
        bot.answer_callback_query(call.id, "Open the bot in private chat to verify.", show_alert=True)
        return
    ok, missing = is_joined_to_required_channels(call.from_user.id)
    if not ok:
        bot.answer_callback_query(call.id, "Please join all required channels first.", show_alert=True)
        send_join_required_ui(call.message, edit=True, missing=missing)
        return
    bot.answer_callback_query(call.id, "Verification successful! ✅")
    success = "✅ <b>Verification Successful!</b>\n\nWelcome! 🎉 Ab aap bot ke features use kar sakte hain."
    try:
        bot.edit_message_text(success, call.message.chat.id, call.message.message_id, parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, success, parse_mode="HTML")

# ---------------- COMMANDS ----------------
def admin_command_allowed(message, command):
    if message.chat.type not in ("group","supergroup"):
        return True
    if not is_admin_or_owner(message.chat.id,message.from_user.id):
        # Delete unauthorized command silently to prevent command-spam clutter.
        delete_message_safe(message.chat.id,message.message_id,retries=2)
        return False
    if command_is_spam(message.chat.id,message.from_user.id,command):
        delete_message_safe(message.chat.id,message.message_id,retries=2)
        return False
    return True

@bot.message_handler(commands=["start"])
def start_handler(message):
    get_user_data(message.from_user.id)
    if message.chat.type in ("group","supergroup"):
        bot.reply_to(message, "Heyy ✨ Main yahan advanced security ke saath hoon. Group AI chat ke liye Admin <code>/aiwake</code> aur silent mode ke liye <code>/aisleep</code> use kar sakte hain.", parse_mode="HTML", reply_markup=telebot.types.ReplyKeyboardRemove())
    else:
        if not private_access_allowed(message):
            return
        bot.reply_to(message, "Heyy ✨ Main yahan hoon. Batao, kya baat karni hai?", reply_markup=telebot.types.ReplyKeyboardRemove())

@bot.message_handler(commands=["aiwake"])
def aiwake(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"aiwake"): return
    settings=get_group_settings(message.chat.id)
    delete_message_safe(message.chat.id,message.message_id,retries=2)
    if settings["ai_enabled"]:
        return
    set_group_setting(message.chat.id,"ai_enabled",True)
    send_temporary(message.chat.id,"✨ I'm awake now. Baat karni ho to mujhe mention, reply, ya mere naam se directly bula lena.",ACTION_DELETE_SECONDS,reply_markup=telebot.types.ReplyKeyboardRemove())

@bot.message_handler(commands=["aisleep"])
def aisleep(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"aisleep"): return
    delete_message_safe(message.chat.id,message.message_id,retries=2)
    if not get_group_settings(message.chat.id)["ai_enabled"]: return
    set_group_setting(message.chat.id,"ai_enabled",False)
    send_temporary(message.chat.id,"🌙 Okay, I'll stay quiet now. Security protection active rahegi.",ACTION_DELETE_SECONDS,reply_markup=telebot.types.ReplyKeyboardRemove())

@bot.message_handler(commands=["allowlinks"])
def allow_links(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"allowlinks"): return
    target=get_reply_target(message)
    if not target: send_temporary(message.chat.id,"Reply to a user's message with <code>/allowlinks 2h</code> or <code>/allowlinks permanent</code>.",ACTION_DELETE_SECONDS,parse_mode="HTML"); return
    parts=(message.text or "").split(maxsplit=1); arg=parts[1].strip().lower() if len(parts)>1 else "permanent"
    expires=0
    if arg not in ("permanent","perm","forever"):
        m=re.fullmatch(r"(\d+)\s*([mhd])",arg)
        if not m: send_temporary(message.chat.id,"Use: <code>/allowlinks 30m</code>, <code>2h</code>, <code>1d</code> or <code>permanent</code>.",ACTION_DELETE_SECONDS,parse_mode="HTML"); return
        n,u=int(m.group(1)),m.group(2); expires=int(time.time()+n*({"m":60,"h":3600,"d":86400}[u]))
    with sqlite3.connect("maya_memory.db",timeout=10) as conn:
        conn.execute("INSERT OR REPLACE INTO link_permissions(chat_id,user_id,expires_at,granted_by) VALUES(?,?,?,?)",(message.chat.id,target.id,expires,message.from_user.id)); conn.commit()
    delete_message_safe(message.chat.id,message.message_id,retries=2)
    duration="permanently" if not expires else arg
    send_temporary(message.chat.id,f"✅ {mention_html(target)} is allowed to send links for <b>{duration}</b>.",ACTION_DELETE_SECONDS,parse_mode="HTML")

@bot.message_handler(commands=["disallowlinks","revokelinks"])
def disallow_links(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"disallowlinks"): return
    target=get_reply_target(message)
    if not target: return
    with sqlite3.connect("maya_memory.db",timeout=10) as conn:
        conn.execute("DELETE FROM link_permissions WHERE chat_id=? AND user_id=?",(message.chat.id,target.id)); conn.commit()
    delete_message_safe(message.chat.id,message.message_id,retries=2)
    send_temporary(message.chat.id,f"🔒 Link permission removed for {mention_html(target)}.",ACTION_DELETE_SECONDS,parse_mode="HTML")

@bot.message_handler(commands=["allowbotadd"])
def allow_bot_add(message):
    if message.chat.type not in ("group", "supergroup") or not admin_command_allowed(message, "allowbotadd"):
        return
    target = get_reply_target(message)
    if not target:
        send_temporary(message.chat.id, "Reply to a user's message with <code>/allowbotadd 2h</code> or <code>/allowbotadd permanent</code>.", ACTION_DELETE_SECONDS, parse_mode="HTML")
        return
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else "permanent"
    expires = 0
    if arg not in ("permanent", "perm", "forever"):
        m = re.fullmatch(r"(\d+)\s*([mhd])", arg)
        if not m:
            send_temporary(message.chat.id, "Use: <code>/allowbotadd 30m</code>, <code>2h</code>, <code>1d</code> or <code>permanent</code>.", ACTION_DELETE_SECONDS, parse_mode="HTML")
            return
        n, u = int(m.group(1)), m.group(2)
        expires = int(time.time() + n * {"m": 60, "h": 3600, "d": 86400}[u])
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        conn.execute("INSERT OR REPLACE INTO bot_add_permissions(chat_id,user_id,expires_at,granted_by) VALUES(?,?,?,?)", (message.chat.id, target.id, expires, message.from_user.id))
        conn.commit()
    delete_message_safe(message.chat.id, message.message_id, retries=2)
    duration = "permanently" if not expires else arg
    send_temporary(message.chat.id, f"🤖 {mention_html(target)} is allowed to add bots for <b>{duration}</b>.", ACTION_DELETE_SECONDS, parse_mode="HTML")

@bot.message_handler(commands=["disallowbotadd", "revokebotadd"])
def disallow_bot_add(message):
    if message.chat.type not in ("group", "supergroup") or not admin_command_allowed(message, "disallowbotadd"):
        return
    target = get_reply_target(message)
    if not target:
        return
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        conn.execute("DELETE FROM bot_add_permissions WHERE chat_id=? AND user_id=?", (message.chat.id, target.id))
        conn.commit()
    delete_message_safe(message.chat.id, message.message_id, retries=2)
    send_temporary(message.chat.id, f"🤖 Bot-add permission removed for {mention_html(target)}.", ACTION_DELETE_SECONDS, parse_mode="HTML")

@bot.message_handler(commands=["trusted"])
def trusted_user(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"trusted"): return
    target=get_reply_target(message)
    if not target:return
    with sqlite3.connect("maya_memory.db",timeout=10) as conn:
        conn.execute("INSERT OR REPLACE INTO trusted_users(chat_id,user_id,granted_by,created_at) VALUES(?,?,?,?)",(message.chat.id,target.id,message.from_user.id,int(time.time())));conn.commit()
    delete_message_safe(message.chat.id,message.message_id,retries=2); send_temporary(message.chat.id,f"✅ {mention_html(target)} added as trusted user.",ACTION_DELETE_SECONDS,parse_mode="HTML")

@bot.message_handler(commands=["untrusted"])
def untrusted_user(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"untrusted"): return
    target=get_reply_target(message)
    if not target:return
    with sqlite3.connect("maya_memory.db",timeout=10) as conn: conn.execute("DELETE FROM trusted_users WHERE chat_id=? AND user_id=?",(message.chat.id,target.id));conn.commit()
    delete_message_safe(message.chat.id,message.message_id,retries=2); send_temporary(message.chat.id,f"🔒 Trusted status removed for {mention_html(target)}.",ACTION_DELETE_SECONDS,parse_mode="HTML")

@bot.message_handler(commands=["lock"])
def lock_handler(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"lock"): return
    parts=(message.text or "").split();
    if len(parts)<2 or parts[1].lower() not in ("media","forwards"): send_temporary(message.chat.id,"Usage: <code>/lock media</code> or <code>/lock forwards</code>",ACTION_DELETE_SECONDS,parse_mode="HTML"); return
    key="lock_media" if parts[1].lower()=="media" else "lock_forwards"; set_group_setting(message.chat.id,key,True); delete_message_safe(message.chat.id,message.message_id,retries=2); send_temporary(message.chat.id,f"🔒 {parts[1].lower()} lock enabled.",ACTION_DELETE_SECONDS)

@bot.message_handler(commands=["unlock"])
def unlock_handler(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"unlock"): return
    parts=(message.text or "").split();
    if len(parts)<2 or parts[1].lower() not in ("media","forwards"): return
    key="lock_media" if parts[1].lower()=="media" else "lock_forwards"; set_group_setting(message.chat.id,key,False); delete_message_safe(message.chat.id,message.message_id,retries=2); send_temporary(message.chat.id,f"🔓 {parts[1].lower()} lock disabled.",ACTION_DELETE_SECONDS)


@bot.message_handler(commands=["mute"])
def manual_mute(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"mute"): return
    target=get_reply_target(message)
    if not target:return
    ok=mute_user(message.chat.id,target.id)
    delete_message_safe(message.chat.id,message.message_id,retries=2)
    if ok: send_temporary(message.chat.id,f"🔇 <b>User Muted</b>\n\n{mention_html(target)} has been temporarily muted.",ACTION_DELETE_SECONDS,parse_mode="HTML")

@bot.message_handler(commands=["security"])
def security_status(message):
    if message.chat.type not in ("group","supergroup") or not is_admin_or_owner(message.chat.id,message.from_user.id): return
    st=get_group_settings(message.chat.id)
    on=lambda v: "🟢 ON" if v else "🔴 OFF"
    text=(f"🛡️ <b>Security Status</b>\n\n🔗 Zero-Link: 🟢 ON\n📢 Anti-Promotion: 🟢 ON\n🤬 Anti-Abuse: 🟢 ON\n🌊 Anti-Flood: 🟢 ON\n🔁 Duplicate Spam: 🟢 ON\n🔠 Anti-Caps: 🟢 ON\n@️⃣ Anti-Mention: 🟢 ON\n💾 Persistent Strikes: 🟢 ON\n🤖 AI Chat: {on(st['ai_enabled'])}\n🔒 Media Lock: {on(st['lock_media'])}\n📨 Forward Lock: {on(st['lock_forwards'])}")
    send_temporary(message.chat.id,text,ACTION_DELETE_SECONDS,parse_mode="HTML")

@bot.message_handler(commands=["userinfo"])
def userinfo_command(message):
    if message.chat.type not in ("group","supergroup") or not is_admin_or_owner(message.chat.id,message.from_user.id): return
    target=get_reply_target(message)
    if not target:return
    with sqlite3.connect("maya_memory.db",timeout=10) as conn:
        row=conn.execute("SELECT strike_count,last_reason FROM strikes WHERE chat_id=? AND user_id=?",(message.chat.id,target.id)).fetchone()
    strikes=row[0] if row else 0; reason=row[1] if row else "None"
    perm="Yes" if has_link_permission(message.chat.id,target.id) else "No"; trusted="Yes" if is_trusted(message.chat.id,target.id) else "No"
    send_temporary(message.chat.id,f"👤 <b>User Info</b>\n\n{mention_html(target)}\n⚠️ Strikes: <b>{strikes}/5</b>\n🔗 Link permission: {perm}\n⭐ Trusted: {trusted}\n📝 Last reason: {html.escape(str(reason))}",ACTION_DELETE_SECONDS,parse_mode="HTML")

@bot.message_handler(commands=["unmute"])
def unmute_user_command(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"unmute"): return
    target=get_reply_target(message)
    if not target:return
    try:
        bot.restrict_chat_member(message.chat.id,target.id,permissions=telebot.types.ChatPermissions(can_send_messages=True,can_send_audios=True,can_send_documents=True,can_send_photos=True,can_send_videos=True,can_send_video_notes=True,can_send_voice_notes=True,can_send_polls=True,can_send_other_messages=True,can_add_web_page_previews=True,can_change_info=False,can_invite_users=True,can_pin_messages=False))
        delete_message_safe(message.chat.id,message.message_id,retries=2); send_temporary(message.chat.id,f"🔊 <b>User Unmuted</b>\n\n{mention_html(target)} can now send messages again.",ACTION_DELETE_SECONDS,parse_mode="HTML")
    except Exception as e: print("UNMUTE ERROR:",e)

@bot.message_handler(commands=["ban"])
def ban_user(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"ban"): return
    target=get_reply_target(message)
    if not target:return
    deleted=delete_tracked_violations(message.chat.id,target.id,True); delete_message_safe(message.chat.id,message.reply_to_message.message_id,retries=6)
    try: bot.ban_chat_member(message.chat.id,target.id)
    except Exception as e: print("BAN ERROR:",e)
    delete_message_safe(message.chat.id,message.message_id,retries=2); send_temporary(message.chat.id,f"🔨 <b>User Banned</b>\n\n{mention_html(target)} has been removed.\n🧹 {deleted} tracked suspicious messages cleaned.",ACTION_DELETE_SECONDS,parse_mode="HTML")

@bot.message_handler(commands=["kick"])
def kick_user(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"kick"): return
    target=get_reply_target(message)
    if not target:return
    deleted=delete_tracked_violations(message.chat.id,target.id,True); delete_message_safe(message.chat.id,message.reply_to_message.message_id,retries=6)
    try: bot.ban_chat_member(message.chat.id,target.id); bot.unban_chat_member(message.chat.id,target.id)
    except Exception as e: print("KICK ERROR:",e)
    delete_message_safe(message.chat.id,message.message_id,retries=2); send_temporary(message.chat.id,f"👢 <b>User Kicked</b>\n\n{mention_html(target)} has been removed.\n🧹 {deleted} tracked suspicious messages cleaned.",ACTION_DELETE_SECONDS,parse_mode="HTML")

@bot.message_handler(commands=["delete","remove"])
def delete_user_history(message):
    if message.chat.type not in ("group","supergroup") or not admin_command_allowed(message,"delete"): return
    target=get_reply_target(message)
    if not target:return
    # Always delete the replied message, then force-rescan the user's complete tracked history.
    direct=delete_message_safe(message.chat.id,message.reply_to_message.message_id,retries=6)
    deleted=delete_tracked_violations(message.chat.id,target.id,True)
    delete_message_safe(message.chat.id,message.message_id,retries=2)
    send_temporary(message.chat.id,f"🧹 <b>Cleanup Complete</b>\n\n{mention_html(target)} ke {deleted + (1 if direct else 0)} suspicious/direct message(s) removed.",ACTION_DELETE_SECONDS,parse_mode="HTML")

@bot.message_handler(commands=["help"])
def help_handler(message):
    text=("🛡️ <b>Advanced Security & AI</b>\n\n💬 <code>/aiwake</code> • <code>/aisleep</code>\n🔗 <code>/allowlinks 2h</code> • <code>/disallowlinks</code>\n🤖 <code>/allowbotadd 2h</code> • <code>/disallowbotadd</code>\n👤 <code>/trusted</code> • <code>/untrusted</code>\n🧹 <code>/delete</code> reply = deep cleanup\n🚫 <code>/ban</code> • 👢 <code>/kick</code> • 🔊 <code>/unmute</code>\n🔒 <code>/lock media</code> • <code>/lock forwards</code>\n\nAuto: zero-link, promotion, abuse, flood, duplicates, caps, mentions, persistent strikes, progressive mute/ban, historical cleanup, leave cleanup.")
    bot.reply_to(message,text,parse_mode="HTML",reply_markup=telebot.types.ReplyKeyboardRemove())

@bot.message_handler(commands=["id"])
def get_id_handler(message): bot.reply_to(message,f"👤 User ID: <code>{message.from_user.id}</code>\n👥 Chat ID: <code>{message.chat.id}</code>",parse_mode="HTML")

def get_reply_target(message): return message.reply_to_message.from_user if message.reply_to_message and message.reply_to_message.from_user else None


# ---------------- MEMBER JOIN / BOT ADD ----------------
@bot.message_handler(content_types=["new_chat_members"])
def welcome_new_members(message):
    chat_name=html.escape(message.chat.title or "this group")
    for member in message.new_chat_members:
        if member.id==bot.get_me().id:
            intro=bot.reply_to(message,f"✨ <b>Security active in {chat_name}</b>\nAI chat: Admin <code>/aiwake</code>",parse_mode="HTML",reply_markup=telebot.types.ReplyKeyboardRemove()); delete_later(message.chat.id,intro.message_id,WELCOME_DELETE_SECONDS); continue
        if getattr(member,"is_bot",False):
            # A normal member can be explicitly whitelisted to add a bot without becoming admin.
            adder = message.from_user
            allowed = bool(adder and (is_admin_or_owner(message.chat.id, adder.id) or has_bot_add_permission(message.chat.id, adder.id)))
            if allowed:
                notice = send_temporary(message.chat.id, f"🤖 Bot addition allowed for {mention_html(adder)}.", ACTION_DELETE_SECONDS, parse_mode="HTML")
                delete_later(message.chat.id, message.message_id, ACTION_DELETE_SECONDS)
                continue
            try:
                bot.ban_chat_member(message.chat.id,member.id); bot.unban_chat_member(message.chat.id,member.id)
                delete_later(message.chat.id,message.message_id,ACTION_DELETE_SECONDS)
            except Exception as e: print("BOT ADD REMOVE:",e)
            continue
        welcome=(f"👋 Hey {mention_html(member)}!\n\n✨ Welcome to <b>{chat_name}</b> 🌻\n\n"
                 "Yahan sab members ka respect karein aur group rules follow karein.\n"
                 "🚫 <b>Links, promotions, spam aur abusing allowed nahi hai.</b>\n"
                 "Agar kisi ko link share karna ho to pehle Owner/Admins se permission le.\n\nEnjoy your stay! 🫶")
        sent=bot.send_message(message.chat.id,welcome,parse_mode="HTML"); delete_later(message.chat.id,sent.message_id,WELCOME_DELETE_SECONDS); delete_later(message.chat.id,message.message_id,WELCOME_DELETE_SECONDS)

# ---------------- LEAVE CLEANUP ----------------
@bot.message_handler(content_types=["left_chat_member"])
def member_left_handler(message):
    member = getattr(message, "left_chat_member", None)
    if not member or message.chat.type not in ("group", "supergroup"):
        return

    # Remove all messages the bot has tracked for this user, including old links/promotions.
    deleted_all = delete_all_tracked_messages(message.chat.id, member.id)
    deleted_suspicious = delete_tracked_violations(message.chat.id, member.id, True)
    total = deleted_all + deleted_suspicious

    notice = (
        f"👋 <b>{mention_html(member)} left the group.</b>\n\n"
        f"🧹 Old tracked messages, links aur suspicious content cleanup complete.\n"
        f"🗑️ <b>{total}</b> message(s) removed."
    )
    send_temporary(message.chat.id, notice, ACTION_DELETE_SECONDS, parse_mode="HTML")
    # Telegram's own leave service message also disappears shortly afterwards.
    delete_later(message.chat.id, message.message_id, ACTION_DELETE_SECONDS)

# ---------------- MAIN MESSAGE HANDLER ----------------
@bot.message_handler(
    func=lambda message: True,
    content_types=["text", "photo", "video", "document", "audio", "voice", "sticker", "animation"],
)
def main_message_handler(message):
    chat_type = message.chat.type
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else None

    # Ignore every message sent by another bot. This prevents bot-to-bot AI loops,
    # command noise and accidental moderation/AI interactions with service bots.
    if not message.from_user or getattr(message.from_user, "is_bot", False):
        return

    if chat_type in ("group", "supergroup"):
        privileged = is_admin_or_owner(chat_id, user_id)
        settings = get_group_settings(chat_id)
        text_content = message.text or message.caption or ""
        trusted = is_trusted(chat_id, user_id)
        link_allowed = has_link_permission(chat_id, user_id)

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
                bypass = trusted or (reason_type in ("link", "promotion") and link_allowed)
                if not bypass:
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

        update_social_context(message)
        if not settings["ai_enabled"]:
            return
        if not should_ai_reply_in_group(message, text_content):
            return

    # Private inbox is protected by mandatory channel verification.
    if chat_type == "private" and not private_access_allowed(message):
        return

    # Private inbox or enabled group AI chat
    user_text = message.text or message.caption
    if not user_text:
        return

    history, rel_level, current_emotion = get_user_data(user_id)
    group_context = []
    if chat_type in ("group", "supergroup"):
        group_context, _ = get_group_context(chat_id)

    try:
        bot.send_chat_action(chat_id, "typing")
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        # Fetch the current Telegram identity at response time. This keeps the
        # AI's self-name correct even when the bot display name changes later.
        ident = get_bot_identity(force=True)
        current_display_name = (ident.get("name") or "Friend").strip()
        current_username = (ident.get("username") or "").strip()
        runtime_identity = (
            "RUNTIME TELEGRAM IDENTITY (authoritative):\n"
            f"- Display name: {current_display_name}\n"
            f"- Username/handle: @{current_username if current_username else 'none'}\n"
            "Use the Display name as your personal name whenever asked. "
            "Do not substitute the username, creator name, project name or any remembered name."
        )

        payload = {
            "model": MODEL_ID,
            "messages": [
                {"role": "system", "content": f"{BASE_SYSTEM_PROMPT}\n\n{runtime_identity}\n\n[Relationship level: {rel_level}]\nPersonal memory:\n{history}\n\nRecent group context (may contain messages between other humans; do not interrupt unless the current message warrants it):\n{json.dumps(group_context[-12:], ensure_ascii=False)}"},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.85,
            "max_tokens": 300,
        }

        # Private chat and group chat both use the same robust completion path.
        # Empty/error responses are retried and never silently degrade into "Hmm...".
        fallback_text = fallback_ai_reply(user_text, current_display_name)
        ai_reply = request_ai_completion(headers, payload, fallback_text)

        updated_history = history + f"\nUser: {user_text}\nAI: {ai_reply}"
        if len(updated_history) > 2000:
            updated_history = updated_history[-2000:]

        update_user_data(user_id, updated_history, rel_level, current_emotion)
        # Natural tiny variation; avoid robotic instant replies.
        if chat_type in ("group", "supergroup"):
            time.sleep(random.uniform(0.6, 2.0))
            with sqlite3.connect("maya_memory.db", timeout=10) as conn:
                conn.execute("INSERT INTO group_context(chat_id,recent_context,last_bot_reply) VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET last_bot_reply=excluded.last_bot_reply", (chat_id, json.dumps(group_context, ensure_ascii=False), int(time.time())))
                conn.commit()
        if chat_type in ("group", "supergroup"):
            activate_ai_conversation(chat_id, user_id)
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
    cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
    cleanup_thread.start()
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    print("✨ Advanced Security & AI Bot is running...")
    bot.infinity_polling(skip_pending=False)
