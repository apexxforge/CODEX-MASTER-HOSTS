import os
import time
import threading
import sqlite3
import re
import unicodedata
from collections import defaultdict, deque
import requests
import telebot
from telebot import types
from flask import Flask

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8936179334:AAForzGiP4SavdrSSUZ4wwQj5MxDwyOHhZs")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
ADMIN_LOG_CHAT_ID = os.getenv("ADMIN_LOG_CHAT_ID", "").strip()
MODEL_ID = "openai/gpt-oss-20b"
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

CHANNELS_FILE = "channels.txt"
DB_FILE = "alyabot_memory.db"
COMMAND_DELETE_DELAY = 10
WARNING_DELETE_DELAY = 10
WELCOME_DELETE_DELAY = 15
FLOOD_WINDOW = 8
FLOOD_LIMIT = 6
DUPLICATE_WINDOW = 45
MAX_MENTIONS = 5

active_group_chats = set()
message_times = defaultdict(deque)
duplicate_cache = defaultdict(lambda: defaultdict(deque))

# ---------------- FORCE JOIN ----------------
def get_required_channels():
    channels = []
    try:
        if os.path.exists(CHANNELS_FILE):
            with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    link = line.strip()
                    if link and not link.startswith("#"):
                        channels.append(link)
    except Exception as e:
        print("Channels File Error:", e)
    return channels

def get_channel_username(link):
    link = link.strip()
    if "t.me/" in link:
        name = link.split("t.me/")[-1].split("?")[0].strip("/")
        if name and not name.startswith("+"):
            return "@" + name
    return link if link.startswith("@") else None

def is_user_joined_all_channels(user_id):
    missing = []
    for link in get_required_channels():
        username = get_channel_username(link)
        if not username:
            continue
        try:
            status = bot.get_chat_member(username, user_id).status
            if status not in ("creator", "owner", "administrator", "member", "restricted"):
                missing.append(link)
        except Exception as e:
            print("Join verification:", e)
            missing.append(link)
    return not missing, missing

def send_force_join_message(message, missing=None):
    channels = missing or get_required_channels()
    markup = types.InlineKeyboardMarkup(row_width=1)
    for i, link in enumerate(channels, 1):
        markup.add(types.InlineKeyboardButton(f"📢 Join Channel {i}", url=link))
    markup.add(types.InlineKeyboardButton("✅ Verify Join", callback_data="verify_force_join"))
    bot.send_message(message.chat.id,
        "🔐 **Access Locked!**\n\nAlya AI use karne se pehle required channels join karein. 🌸\n\n👇 Sabhi channels join karke **Verify Join** dabayein.",
        parse_mode="Markdown", reply_markup=markup)

# ---------------- DATABASE ----------------
def init_db():
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS memory (
            user_id INTEGER PRIMARY KEY, history TEXT,
            relationship_level INTEGER DEFAULT 1,
            emotional_state TEXT DEFAULT 'happy & playful')""")
        c.execute("""CREATE TABLE IF NOT EXISTS strikes (
            chat_id INTEGER, user_id INTEGER, count INTEGER DEFAULT 0,
            last_reason TEXT, updated_at INTEGER,
            PRIMARY KEY(chat_id,user_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS group_settings (
            chat_id INTEGER PRIMARY KEY, media_locked INTEGER DEFAULT 0,
            forwards_locked INTEGER DEFAULT 1)""")
        conn.commit()
init_db()

def get_user_data(user_id):
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        c = conn.cursor(); c.execute("SELECT history,relationship_level,emotional_state FROM memory WHERE user_id=?", (user_id,))
        row = c.fetchone()
        if not row:
            c.execute("INSERT INTO memory VALUES(?,?,?,?)", (user_id,"",1,"happy & playful")); conn.commit()
            row = ("",1,"happy & playful")
        return row

def update_user_data(user_id, history, rel, emotion):
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.execute("UPDATE memory SET history=?,relationship_level=?,emotional_state=? WHERE user_id=?", (history,rel,emotion,user_id)); conn.commit()

def get_strikes(chat_id, user_id):
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        row = conn.execute("SELECT count FROM strikes WHERE chat_id=? AND user_id=?", (chat_id,user_id)).fetchone()
        return row[0] if row else 0

def add_strike(chat_id, user_id, reason):
    now = int(time.time())
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        c = conn.cursor(); current = get_strikes(chat_id,user_id) + 1
        c.execute("INSERT INTO strikes(chat_id,user_id,count,last_reason,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET count=excluded.count,last_reason=excluded.last_reason,updated_at=excluded.updated_at", (chat_id,user_id,current,reason,now)); conn.commit()
        return current

def reset_strikes(chat_id,user_id):
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.execute("DELETE FROM strikes WHERE chat_id=? AND user_id=?", (chat_id,user_id)); conn.commit()

def get_setting(chat_id, key, default=False):
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.execute("INSERT OR IGNORE INTO group_settings(chat_id) VALUES(?)", (chat_id,))
        row=conn.execute(f"SELECT {key} FROM group_settings WHERE chat_id=?",(chat_id,)).fetchone(); return bool(row[0]) if row else default

def set_setting(chat_id,key,value):
    if key not in ("media_locked","forwards_locked"): return
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.execute("INSERT OR IGNORE INTO group_settings(chat_id) VALUES(?)", (chat_id,))
        conn.execute(f"UPDATE group_settings SET {key}=? WHERE chat_id=?", (int(bool(value)),chat_id)); conn.commit()

# ---------------- HELPERS ----------------
def mention(user):
    name = (user.first_name or user.username or "User").replace("[","(").replace("]",")")
    return f"[{name}](tg://user?id={user.id})"

def is_admin_or_owner(chat_id,user_id):
    try:
        return bot.get_chat_member(chat_id,user_id).status in ("creator","owner","administrator")
    except Exception: return False

def auto_delete_message(chat_id,message_id,delay=10):
    def task():
        try:
            bot.delete_message(chat_id,message_id)
            print(f"Auto-deleted {message_id} in {chat_id}")
        except Exception as e:
            print(f"Auto-delete error {chat_id}/{message_id}: {e}")
    t=threading.Timer(delay,task); t.daemon=True; t.start()

def temporary_command_reply(message,text,delay=COMMAND_DELETE_DELAY,**kwargs):
    sent=bot.reply_to(message,text,**kwargs)
    if message.chat.type in ("group","supergroup"):
        auto_delete_message(message.chat.id,message.message_id,delay)
        auto_delete_message(message.chat.id,sent.message_id,delay)
    return sent

def admin_log(text):
    if ADMIN_LOG_CHAT_ID:
        try: bot.send_message(int(ADMIN_LOG_CHAT_ID),text,parse_mode="Markdown")
        except Exception as e: print("Admin log error:",e)

# ---------------- NORMALIZATION / DETECTION ----------------
ZERO_WIDTH = "\u200b\u200c\u200d\ufeff\u2060"
def normalize(text):
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = ''.join(ch for ch in text if ch not in ZERO_WIDTH and unicodedata.category(ch) != 'Cf')
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def compact(text):
    t=normalize(text)
    t=t.replace("[dot]",".").replace("(dot)",".").replace("{dot}",".")
    t=re.sub(r'\bdot\b','.',t)
    return re.sub(r'[\s_~|\\`*\-]+','',t)

COMMON_TLDS = r'(?:com|org|net|in|io|gg|me|tv|co|app|dev|xyz|info|biz|online|site|link|live|pro|store|shop|tech|ai|ly|us|uk|ru|de|jp|cn|cc)'
URL_RE = re.compile(r'(?:https?://|ftp://|www\.)[^\s]+|(?:[a-z0-9-]+\.)+'+COMMON_TLDS+r'(?:/[^\s]*)?',re.I)
HANDLE_RE = re.compile(r'(?<![\w@])@[a-zA-Z0-9_]{4,}(?!\w)')
PLATFORM_WORDS = ("telegram","youtube","instagram","facebook","discord","whatsapp","snapchat","twitter","x.com","tiktok","channel","group","server")
PROMO_PATTERNS = [
    r'\bjoin\s+(my|our|mera|mere|hamara|the)\s*(channel|group|telegram|server)',
    r'\b(dm|d\s*m|inbox|message)\s*(me|karo|karna|for)',
    r'\b(panel|config|service|account|hack|mod)\s*(available|provide|selling|sale|chahiye)',
    r'\b(subscribe|follow)\s+(me|my|our|channel)',
    r'(mere|mera|hamara)\s+(channel|group)\s+(join|dekho|follow)',
    r'\bcontact\s+me\b', r'\bpaid\s+(service|panel|config)\b'
]
ABUSE_WORDS = {
 'madarchod','maderchod','bhenchod','behenchod','bhosdike','bhosdi','gandu','chutiya','chutia','lauda','lund','randi','harami','kamina','bkl','bsdk','mc','bc','asshole','bastard','bitch','fuck','fucker','motherfucker','dickhead','bullshit','cunt','whore','slut','idiot'
}

def abuse_detected(text):
    t=normalize(text); words=re.findall(r"[\w']+",t)
    if any(w in ABUSE_WORDS for w in words): return True
    joined=re.sub(r'[^a-z0-9]','',t)
    return any(w in joined for w in ABUSE_WORDS if len(w)>=4)

def detect_violation(text, entities=None):
    if not text: return None
    raw=normalize(text); c=compact(text)
    # Telegram URL entities catch links even when text is unusual/hidden.
    if entities:
        for e in entities:
            if getattr(e,'type','') in ('url','text_link','mention','text_mention'):
                return 'External link or username'
    if URL_RE.search(raw) or URL_RE.search(c): return 'External link'
    if HANDLE_RE.search(raw): return 'External username / handle'
    if re.search(r'(?:t\.?me|telegram\.?(?:me|org)|youtu\.?be|youtube\.?com|instagram\.?com|discord\.?gg|discordapp\.?com|wa\.?me|whatsapp\.?(?:com|me)|bit\.?ly|tinyurl\.?com)', c): return 'Platform link'
    if any(re.search(p,raw) for p in PROMO_PATTERNS): return 'Promotion / solicitation'
    if abuse_detected(raw): return 'Abusive language'
    return None

def is_flood(chat_id,user_id):
    key=(chat_id,user_id); now=time.time(); q=message_times[key]; q.append(now)
    while q and now-q[0]>FLOOD_WINDOW: q.popleft()
    return len(q)>FLOOD_LIMIT

def is_duplicate(chat_id,user_id,text):
    val=compact(text)
    if len(val)<4:return False
    key=(chat_id,user_id); now=time.time(); q=duplicate_cache[key][val]; q.append(now)
    while q and now-q[0]>DUPLICATE_WINDOW:q.popleft()
    return len(q)>=3

def caps_spam(text):
    letters=[x for x in (text or '') if x.isalpha()]
    if len(letters)<12:return False
    return sum(x.isupper() for x in letters)/len(letters)>=0.82

def mention_spam(text,entities=None):
    count=len(HANDLE_RE.findall(text or ''))
    if entities: count += sum(1 for e in entities if getattr(e,'type','') in ('mention','text_mention'))
    return count>MAX_MENTIONS

# ---------------- MODERATION ----------------
def delete_and_warn(message,reason):
    chat_id=message.chat.id; user=message.from_user
    deleted=False
    try:
        bot.delete_message(chat_id,message.message_id); deleted=True
    except Exception as e:
        print(f"ORIGINAL DELETE FAILED chat={chat_id} msg={message.message_id}: {e}")
    strikes=add_strike(chat_id,user.id,reason)
    if strikes>=5:
        try:
            bot.ban_chat_member(chat_id,user.id)
            text=f"🚫 {mention(user)} has been banned after repeated violations.\nReason: **{reason}**"
            reset_strikes(chat_id,user.id)
        except Exception as e:
            text=f"🚨 {mention(user)} repeated violations detected.\nReason: **{reason}**"
            print("Ban error:",e)
    elif strikes>=3:
        try:
            bot.restrict_chat_member(chat_id,user.id,can_send_messages=False,until_date=int(time.time())+600)
            text=f"🔇 {mention(user)} has been muted for 10 minutes due to repeated violations.\nReason: **{reason}**"
        except Exception as e:
            text=f"⚠️ {mention(user)} repeated violations detected.\nReason: **{reason}**"; print("Mute error:",e)
    else:
        text=(f"⚠️ {mention(user)}\n\nSharing other channel links, panel ads, promotions, or forwarded messages is strictly not allowed here.\n🛡️ Your message has been removed.")
    try:
        warn=bot.send_message(chat_id,text,parse_mode="Markdown"); auto_delete_message(chat_id,warn.message_id,WARNING_DELETE_DELAY)
    except Exception as e: print("Warning error:",e)
    admin_log(f"🛡️ **Alya Action**\nChat: `{chat_id}`\nUser: {mention(user)}\nReason: {reason}\nDeleted: {deleted}\nStrikes: {strikes}")

# ---------------- COMMANDS ----------------
@bot.message_handler(commands=['start'])
def start_handler(message):
    if message.chat.type=='private':
        ok,missing=is_user_joined_all_channels(message.from_user.id)
        if not ok: send_force_join_message(message,missing); return
        get_user_data(message.from_user.id)
        bot.reply_to(message,f"🌸 **Welcome to Alya AI, {message.from_user.first_name} ji!** ✨\n\nMain aapki personal AI companion aur group protection guard hoon, built by **Apex X Forge (@ApexXForge)**. 🚀\n\nGroup me add karke Admin banayein for protection. 🛡️",parse_mode='Markdown')
    else:
        temporary_command_reply(message,"🌸 **Alya — Pro Group Management & AI Guard Active!**\n\nUse `/chaton` to enable AI chat.",parse_mode='Markdown')

@bot.callback_query_handler(func=lambda c:c.data=='verify_force_join')
def verify_force_join(call):
    ok,missing=is_user_joined_all_channels(call.from_user.id)
    if ok:
        try: bot.delete_message(call.message.chat.id,call.message.message_id)
        except Exception: pass
        bot.send_message(call.message.chat.id,f"🎉 **Verification Successful!**\n\n🌸 Welcome {call.from_user.first_name} ji! Ab aap Alya AI use kar sakte hain. ✨",parse_mode='Markdown')
        bot.answer_callback_query(call.id,"Verification Successful! 🎉")
    else:
        bot.answer_callback_query(call.id,"Pehle sabhi required channels join karein!")
        send_force_join_message(call.message,missing)

@bot.message_handler(commands=['chaton'])
def chaton(message):
    if message.chat.type in ('group','supergroup'):
        if is_admin_or_owner(message.chat.id,message.from_user.id): active_group_chats.add(message.chat.id); temporary_command_reply(message,"✨ Group AI chat enabled! 😎🌻")
        else: temporary_command_reply(message,"⚠️ This command is for Admin/Owner only.")

@bot.message_handler(commands=['chatoff'])
def chatoff(message):
    if message.chat.type in ('group','supergroup'):
        if is_admin_or_owner(message.chat.id,message.from_user.id): active_group_chats.discard(message.chat.id); temporary_command_reply(message,"🔒 Group AI chat disabled. Security remains active. 🛡️")
        else: temporary_command_reply(message,"⚠️ This command is for Admin/Owner only.")

@bot.message_handler(commands=['lock','unlock'])
def lock_handler(message):
    if message.chat.type not in ('group','supergroup'): return
    if not is_admin_or_owner(message.chat.id,message.from_user.id): temporary_command_reply(message,"⚠️ Lock settings are for Admin/Owner only."); return
    parts=message.text.lower().split(); action=parts[0][1:]; target=parts[1] if len(parts)>1 else ''
    mapping={'media':'media_locked','forwards':'forwards_locked'}
    if target not in mapping: temporary_command_reply(message,"Usage: `/lock media`, `/unlock media`, `/lock forwards`, `/unlock forwards`",parse_mode='Markdown'); return
    set_setting(message.chat.id,mapping[target],action=='lock')
    temporary_command_reply(message,f"🔒 {target.title()} lock {'enabled' if action=='lock' else 'disabled'}.")

@bot.message_handler(commands=['help'])
def help_handler(message):
    text="🛠️ **Alya Pro Management Panel**\n\n`/chaton` `/chatoff`\n`/mute` `/unmute` `/ban` `/kick` (reply)\n`/lock media` `/unlock media`\n`/lock forwards` `/unlock forwards`\n`/status` `/id`\n\n🛡️ Zero-link, anti-promotion, anti-abuse, anti-flood, duplicate, caps, mention-spam and bot-add protection active."
    temporary_command_reply(message,text,parse_mode='Markdown')

@bot.message_handler(commands=['status','ping'])
def status_handler(message): temporary_command_reply(message,"⚡ **Alya Status:** Online and guarding the community. 🚀",parse_mode='Markdown')
@bot.message_handler(commands=['id'])
def id_handler(message): temporary_command_reply(message,f"👤 User ID: `{message.from_user.id}`\n👥 Chat ID: `{message.chat.id}`",parse_mode='Markdown')

def moderation_command(message,action):
    if message.chat.type not in ('group','supergroup'): return
    if not is_admin_or_owner(message.chat.id,message.from_user.id) or not message.reply_to_message:
        temporary_command_reply(message,"⚠️ Admin rights required. Reply to the target user's message."); return
    target=message.reply_to_message.from_user
    try:
        if action=='mute': bot.restrict_chat_member(message.chat.id,target.id,can_send_messages=False); txt=f"🔇 {mention(target)} has been muted."
        elif action=='unmute': bot.restrict_chat_member(message.chat.id,target.id,can_send_messages=True,can_send_media_messages=True,can_send_other_messages=True,can_add_web_page_previews=True); txt=f"🔊 {mention(target)} has been unmuted."
        elif action=='ban': bot.ban_chat_member(message.chat.id,target.id); txt=f"🚫 {mention(target)} has been permanently banned."
        else: bot.ban_chat_member(message.chat.id,target.id); bot.unban_chat_member(message.chat.id,target.id); txt=f"👢 {mention(target)} has been removed from the group."
        temporary_command_reply(message,txt,parse_mode='Markdown'); admin_log(f"⚙️ **Manual moderation**\nAction: {action}\nUser: {mention(target)}\nChat: `{message.chat.id}`")
    except Exception as e: temporary_command_reply(message,f"⚠️ Error: {e}")

@bot.message_handler(commands=['mute'])
def mute(message): moderation_command(message,'mute')
@bot.message_handler(commands=['unmute'])
def unmute(message): moderation_command(message,'unmute')
@bot.message_handler(commands=['ban'])
def ban(message): moderation_command(message,'ban')
@bot.message_handler(commands=['kick'])
def kick(message): moderation_command(message,'kick')

# ---------------- JOIN / BOT GUARD ----------------
@bot.chat_join_request_handler()
def auto_approve(req):
    try:
        bot.approve_chat_join_request(req.chat.id,req.from_user.id)
        sent=bot.send_message(req.chat.id,f"🎉 **Welcome to {req.chat.title}!**\n\n👤 {mention(req.from_user)}\n🆔 `{req.from_user.id}`",parse_mode='Markdown')
        auto_delete_message(req.chat.id,sent.message_id,WELCOME_DELETE_DELAY)
    except Exception as e: print('Join request:',e)

@bot.message_handler(content_types=['new_chat_members'])
def new_members(message):
    actor=message.from_user
    for member in message.new_chat_members:
        if member.id==bot.get_me().id:
            sent=bot.reply_to(message,"🌸 Alya is active! Make me Admin with Delete Messages and Restrict Members permissions. 🛡️")
            auto_delete_message(message.chat.id,sent.message_id,WELCOME_DELETE_DELAY); continue
        # Block bots added by non-admins.
        if getattr(member,'is_bot',False) and not is_admin_or_owner(message.chat.id,actor.id):
            try:
                bot.ban_chat_member(message.chat.id,member.id); bot.unban_chat_member(message.chat.id,member.id)
            except Exception as e: print('Bot removal:',e)
            try:
                notice=bot.send_message(message.chat.id,f"🤖 Unauthorized bot addition blocked. {mention(actor)}, please ask an Owner/Admin before adding bots.",parse_mode='Markdown'); auto_delete_message(message.chat.id,notice.message_id,WARNING_DELETE_DELAY)
            except Exception: pass
            try:
                bot.send_message(actor.id,"⚠️ Your attempt to add a bot without Owner/Admin permission was blocked. Please get permission before adding bots to the group.")
            except Exception: pass
            admin_log(f"🤖 **Unauthorized bot blocked**\nAdder: {mention(actor)}\nBot: `{member.id}`\nChat: `{message.chat.id}`"); continue
        sent=bot.reply_to(message,f"✨ **Welcome!** 🎉\n\n👤 {mention(member)}\n🆔 `{member.id}`\n\n🛡️ Links, promotions, forwards and abusive language are not allowed.",parse_mode='Markdown')
        auto_delete_message(message.chat.id,sent.message_id,WELCOME_DELETE_DELAY)

@bot.message_handler(content_types=['left_chat_member'])
def left_member(message):
    user=message.left_chat_member
    sent=bot.send_message(message.chat.id,f"👋 {mention(user)} left the group.",parse_mode='Markdown')
    auto_delete_message(message.chat.id,sent.message_id,WELCOME_DELETE_DELAY)

# ---------------- MASTER HANDLER ----------------
@bot.message_handler(func=lambda m:True, content_types=['text','photo','video','document','audio','animation','voice','sticker','contact','location'])
def main_handler(message):
    chat_id=message.chat.id; user=message.from_user; chat_type=message.chat.type
    if chat_type=='private':
        ok,missing=is_user_joined_all_channels(user.id)
        if not ok: send_force_join_message(message,missing); return
    if chat_type in ('group','supergroup'):
        if not is_admin_or_owner(chat_id,user.id):
            text=message.text or message.caption or ''
            entities=(getattr(message,'entities',None) or getattr(message,'caption_entities',None) or [])
            is_forward=bool(getattr(message,'forward_date',None) or getattr(message,'forward_origin',None))
            reason=None
            if is_forward and get_setting(chat_id,'forwards_locked',True): reason='Forwarded content'
            elif get_setting(chat_id,'media_locked',False) and message.content_type in ('photo','video','document','audio','animation','voice','sticker'): reason='Media is temporarily locked'
            else: reason=detect_violation(text,entities)
            if not reason and is_flood(chat_id,user.id): reason='Message flooding'
            if not reason and text and is_duplicate(chat_id,user.id,text): reason='Duplicate spam'
            if not reason and text and caps_spam(text): reason='Caps spam'
            if not reason and mention_spam(text,entities): reason='Mention spam'
            if reason:
                delete_and_warn(message,reason); return
        if chat_id not in active_group_chats: return
    user_text=message.text or message.caption
    if not user_text:return
    history,rel,emotion=get_user_data(user.id)
    try:
        bot.send_chat_action(chat_id,'typing')
        payload={"model":MODEL_ID,"messages":[{"role":"system","content":"You are Alya, a smart friendly AI companion crafted by Apex X Forge. Speak naturally in Hinglish or English matching the user.\nMemory:\n"+history},{"role":"user","content":user_text}],"temperature":0.85,"max_tokens":300}
        r=requests.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"},json=payload,timeout=20)
        data=r.json(); reply=data.get('choices',[{}])[0].get('message',{}).get('content','Acha ji! 😅').strip() or 'Hmm... sun rahi hoon! ✨'
        history=(history+f"\nUser: {user_text}\nAlya: {reply}")[-2000:]; update_user_data(user.id,history,rel,emotion)
        bot.reply_to(message,reply)
    except Exception as e: print('AI Error:',e)

app=Flask(__name__)
@app.route('/')
def home(): return '🌸 Alya Pro Group Management & AI Guard is Live!'
def run_flask(): app.run(host='0.0.0.0',port=int(os.environ.get('PORT',10000)))

if __name__=='__main__':
    threading.Thread(target=run_flask,daemon=True).start()
    print('🌸 Alya Pro Management Bot is running...')
    bot.infinity_polling(skip_pending=True)
