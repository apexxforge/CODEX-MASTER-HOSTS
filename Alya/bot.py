import os
import time
import threading
import sqlite3
import re
import requests
import telebot
from flask import Flask

TELEGRAM_BOT_TOKEN = "8975065411:AAE8wUwhTBEUsq_Mxj2n8XWHHBCtRpodUYA"
GROQ_API_KEY = "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"

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
MODEL_ID = "openai/gpt-oss-20b"

# --- STORAGE ---
active_group_chats = set()
warning_counts = {}  # Tracks user warnings for automated penalties

# --- DATABASE SETUP ---
def init_db():
    with sqlite3.connect("alyabot_memory.db", timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                user_id INTEGER PRIMARY KEY,
                history TEXT,
                relationship_level INTEGER DEFAULT 1,
                emotional_state TEXT DEFAULT 'happy & playful'
            )
        """)
        conn.commit()

init_db()

def get_user_data(user_id):
    with sqlite3.connect("alyabot_memory.db", timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT history, relationship_level, emotional_state FROM memory WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("INSERT INTO memory (user_id, history, relationship_level, emotional_state) VALUES (?, ?, ?, ?)", 
                           (user_id, "", 1, "happy & playful"))
            conn.commit()
            row = ("", 1, "happy & playful")
        return row

def update_user_data(user_id, new_history, rel_level, emotion):
    with sqlite3.connect("alyabot_memory.db", timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE memory SET history = ?, relationship_level = ?, emotional_state = ? WHERE user_id = ?", 
                   (new_history, rel_level, emotion, user_id))
        conn.commit()

# --- SYSTEM PROMPT ---
BASE_SYSTEM_PROMPT = (
    "You are 'Alya', a smart, friendly, and powerful AI group protection and community management assistant crafted by Apex X Forge. "
    "1. Never sound like a robot. Talk like a smart human friend in natural Hinglish or English matching the user. "
    "2. Use expressive emojis naturally. "
    "3. Keep casual chats short and cute. For technical questions, provide clear, beautifully formatted expert solutions. "
    "4. If anyone asks who made you, proudly state you were built by Apex X Forge (@ApexXForge)."
)

# --- HELPER: CHECK ADMIN OR OWNER ---
def is_admin_or_owner(chat_id, user_id):
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ['creator', 'administrator']
    except Exception:
        return False

# --- HELPER: ADVANCED ANTI-BYPASS SPAM & ABUSE DETECTOR ---
def contains_spam_link_or_abuse(text):
    if not text:
        return False
    
    # Deep normalization to catch spaced/hidden bypass attempts
    cleaned_text = re.sub(r'[\s\-_~.]+', '', text.lower())
    cleaned_text = (cleaned_text
                    .replace('[dot]', '')
                    .replace('(dot)', '')
                    .replace('{dot}', '')
                    .replace('dot', ''))

    # Comprehensive Spam, Panel & Unapproved Link Patterns
    link_patterns = [
        r'https?://', r't\.me', r'telegram\.me', r'wa\.me', r'instagram\.com', 
        r'youtube\.com', r'youtu\.be', r'bit\.ly', r'chat\.whatsapp\.com',
        r'vk\.com', r'discord\.gg', r'panel', r'aimbot', r'headshot', 
        r'config', r'hack', r'modapk', r'selling', r'price', r'buynow',
        r'paid', r'inbox', r'joinmychannel', r'dmmeme', r'freefirepanel',
        r'viphack', r'kisikohahiye', r'hacksale'
    ]
    
    for pattern in link_patterns:
        if re.search(pattern, cleaned_text):
            return True
            
    # Strict Abusive Language Filter
    abuse_words = [
        'madarchod', 'bhenchod', 'bhosdike', 'gandu', 'chutiya', 'lauda', 'lund', 
        'randi', 'bkl', 'mc', 'bc', 'bsdk', 'asshole', 'bastard', 'bitch', 'fuck'
    ]
    
    words_in_text = re.findall(r'\b\w+\b', text.lower())
    for word in words_in_text:
        if word in abuse_words:
            return True
            
    return False

# --- COMMANDS: CORE MANAGEMENT & PERSONAL WELCOME ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    get_user_data(user_id)
    
    # Check if command is used in Private Chat (DM) vs Group Chat
    if message.chat.type == 'private':
        personal_welcome = (
            f"🌸 **Welcome to Alya AI, {message.from_user.first_name} ji!** ✨\n\n"
            "Main aapki personal AI companion aur group protection guard hoon, jise **Apex X Forge (@ApexXForge)** ne banaya hai! 🚀\n\n"
            "🤖 **How to use me:**\n"
            "• **In Personal Chat:** Aap mujhse yahan kisi bhi topic par khul kar baatein kar sakte hain, sawaal pooch sakte hain, ya bas dosti kar sakte hain! 💬\n"
            "• **In Groups/Channels:** Mujhe apne group ya channel mein add karein aur **Admin** banayein. Wahan main automatically spam, bypass links, panel ads, aur abuse ko uda doongi! 🛡️\n\n"
            "⚙️ **Group Commands:**\n"
            "• `/chaton` - Group mein meri AI chat on karne ke liye\n"
            "• `/chatoff` - Group mein sirf security active rakhne ke liye\n"
            "• `/help` - Saare management commands dekhne ke liye\n\n"
            "✨ *Boliye, aaj main aapki kya madad kar sakti hoon?*"
        )
        bot.reply_to(message, personal_welcome, parse_mode="Markdown")
    else:
        bot.reply_to(message, "🌸 **Alya 🌸 — Pro Group Management & AI Guard Active!**\n\n🛡️ Full anti-spam, dynamic welcomes, moderation, and AI chat enabled. Use `/chaton` to talk!")

@bot.message_handler(commands=['chaton'])
def enable_group_chat(message):
    if message.chat.type in ['group', 'supergroup']:
        if is_admin_or_owner(message.chat.id, message.from_user.id):
            active_group_chats.add(message.chat.id)
            bot.reply_to(message, "✨ Group chat enabled! Ab aap sabhi mujhse khul kar baatein kar sakte hain 😎🌻")
        else:
            bot.reply_to(message, "⚠️ Arre, yeh command sirf Admin ya Owner hi de sakte hain!")

@bot.message_handler(commands=['chatoff'])
def disable_group_chat(message):
    if message.chat.type in ['group', 'supergroup']:
        if is_admin_or_owner(message.chat.id, message.from_user.id):
            active_group_chats.discard(message.chat.id)
            bot.reply_to(message, "🔒 Group chat band kar di hai. Ab main sirf security aur moderation par dhyan lungi! 🛡️✨")
        else:
            bot.reply_to(message, "⚠️ Sirf Admin ya Owner hi group chat off kar sakte hain!")

@bot.message_handler(commands=['help'])
def help_handler(message):
    help_text = (
        "🛠️ **Alya Pro Management Panel:**\n\n"
        "🛡️ *Security:* Auto-deletes bypass links, panel promotions, and abuses.\n"
        "⚙️ *Admin Commands:*\n"
        "• `/chaton` - Enable AI chat\n"
        "• `/chatoff` - Disable AI chat\n"
        "• `/mute` - Mute user (Reply to msg)\n"
        "• `/unmute` - Unmute user (Reply to msg)\n"
        "• `/ban` - Permanent ban user (Reply to msg)\n"
        "• `/kick` - Kick user (Reply to msg)\n"
        "• `/status` - Check bot status\n"
        "• `/id` - Get User & Chat IDs"
    )
    bot.reply_to(message, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['status', 'ping'])
def status_handler(message):
    bot.reply_to(message, "⚡ **Alya Status:** Online, fully operational, and guarding the community smoothly! 🚀")

@bot.message_handler(commands=['id'])
def get_id_handler(message):
    bot.reply_to(message, f"👤 User ID: `{message.from_user.id}`\n👥 Chat ID: `{message.chat.id}`", parse_mode="Markdown")

# --- MODERATION COMMANDS ---
@bot.message_handler(commands=['mute'])
def mute_user(message):
    if message.chat.type in ['group', 'supergroup']:
        try:
            if is_admin_or_owner(message.chat.id, message.from_user.id) and message.reply_to_message:
                target_id = message.reply_to_message.from_user.id
                bot.restrict_chat_member(message.chat.id, target_id, can_send_messages=False)
                bot.reply_to(message, "🔇 Is user ko mute kar diya gaya hai! 🤫")
            else:
                bot.reply_to(message, "⚠️ Admin rights required, aur target message par reply karein!")
        except Exception as e:
            bot.reply_to(message, f"⚠️ Error: {str(e)}")

@bot.message_handler(commands=['unmute'])
def unmute_user(message):
    if message.chat.type in ['group', 'supergroup']:
        try:
            if is_admin_or_owner(message.chat.id, message.from_user.id) and message.reply_to_message:
                target_id = message.reply_to_message.from_user.id
                bot.restrict_chat_member(message.chat.id, target_id, can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True)
                bot.reply_to(message, "🔊 User ko unmute kar diya hai. Ab woh bol sakte hain! ✨")
            else:
                bot.reply_to(message, "⚠️ Admin rights required, aur target message par reply karein!")
        except Exception as e:
            bot.reply_to(message, f"⚠️ Error: {str(e)}")

@bot.message_handler(commands=['ban'])
def ban_user(message):
    if message.chat.type in ['group', 'supergroup']:
        try:
            if is_admin_or_owner(message.chat.id, message.from_user.id) and message.reply_to_message:
                bot.ban_chat_member(message.chat.id, message.reply_to_message.from_user.id)
                bot.reply_to(message, "🚫 Is user ko group se permanent ban kar diya! 👋")
            else:
                bot.reply_to(message, "⚠️ Admin rights required, aur target message par reply karein!")
        except Exception as e:
            bot.reply_to(message, f"⚠️ Error: {str(e)}")

@bot.message_handler(commands=['kick'])
def kick_user(message):
    if message.chat.type in ['group', 'supergroup']:
        try:
            if is_admin_or_owner(message.chat.id, message.from_user.id) and message.reply_to_message:
                target_id = message.reply_to_message.from_user.id
                bot.kick_chat_member(message.chat.id, target_id)
                bot.unban_chat_member(message.chat.id, target_id)
                bot.reply_to(message, "👢 Bahar ka rasta dikha diya usko!")
            else:
                bot.reply_to(message, "⚠️ Admin rights required, aur target message par reply karein!")
        except Exception as e:
            bot.reply_to(message, f"⚠️ Error: {str(e)}")

# --- AUTO-APPROVE JOIN REQUESTS ---
@bot.chat_join_request_handler()
def auto_approve_join_requests(message):
    try:
        bot.approve_chat_join_request(message.chat.id, message.from_user.id)
        chat_title = message.chat.title
        user_name = message.from_user.first_name
        user_id = message.from_user.id
        
        welcome_text = (
            f"🎉 **Welcome to {chat_title}!** 🌻\n\n"
            f"👤 **User:** [{user_name}](tg://user?id={user_id})\n"
            f"🆔 **Chat ID:** `{user_id}`\n\n"
            "Aapki join request approve ho gayi hai. Group ke rules dhyan se padh lena! ✨"
        )
        bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown")
    except Exception as e:
        print(f"Join Request Error: {str(e)}")

# --- WELCOME NEW MEMBERS WITH GROUP/CHANNEL NAME & ID ---
@bot.message_handler(content_types=['new_chat_members'])
def welcome_new_members(message):
    chat_title = message.chat.title or "this Group/Channel"
    for member in message.new_chat_members:
        if member.id == bot.get_me().id:
            bot.reply_to(message, f"🌸 Hii everyone! Main Alya hoon, aur ab **{chat_title}** full automation security aur anti-spam ke under hai 😎🌻. Chat ke liye `/chaton` use karein!")
        else:
            user_name = member.first_name
            user_id = member.id
            welcome_msg = (
                f"✨ **Welcome to {chat_title}!** 🎉\n\n"
                f"👤 **Member:** [{user_name}](tg://user?id={user_id})\n"
                f"🆔 **User ID:** `{user_id}`\n\n"
                "Yahan unapproved links, panel promotions aur abuse strict allowed nahi hain! 🌻 Dhyan rakhna ji!"
            )
            bot.reply_to(message, welcome_msg, parse_mode="Markdown")

# --- MASTER AUTOMATED MESSAGE HANDLER (DM CHAT + GROUP SECURITY) ---
@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'document', 'audio', 'forwarded'])
def main_message_handler(message):
    chat_type = message.chat.type
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    # 1. GROUP SECURITY & MODERATION LAYER
    if chat_type in ['group', 'supergroup']:
        is_privileged = is_admin_or_owner(chat_id, user_id)
        is_forwarded = message.forward_date is not None
        text_content = message.text or message.caption or ""
        
        has_violation = contains_spam_link_or_abuse(text_content)
        
        # If normal user violates rules -> Instant Delete + Warning + Penalty Escalation
        if not is_privileged and (has_violation or is_forwarded):
            try:
                bot.delete_message(chat_id, message.message_id)
                user_name = message.from_user.first_name
                
                # Increment warning counts
                warning_counts[user_id] = warning_counts.get(user_id, 0) + 1
                
                if warning_counts[user_id] >= 3:
                    # Auto mute on 3rd violation for 10 minutes
                    bot.restrict_chat_member(chat_id, user_id, can_send_messages=False, until_date=int(time.time()) + 600)
                    warn_msg = bot.send_message(
                        chat_id, 
                        f"🚨 [{user_name}](tg://user?id={user_id}) has been automatically muted for 10 minutes due to repeated rule violations! 🛡️", 
                        parse_mode="Markdown"
                    )
                    warning_counts[user_id] = 0
                else:
                    warn_msg = bot.send_message(
                        chat_id, 
                        f"⚠️ Hey [{user_name}](tg://user?id={user_id}), unapproved links, panel ads, or forwards are strictly prohibited! Message removed. 🛡️", 
                        parse_mode="Markdown"
                    )
                
                # Auto-delete warning alert after 5 seconds
                threading.Timer(5, lambda: bot.delete_message(chat_id, warn_msg.message_id)).start()
            except Exception:
                pass
            return  
                
        # 2. GROUP CHAT RESTRICTION CHECK (Only chat if /chaton is active)
        if chat_id not in active_group_chats:
            return 

    # 3. AI COMPANION RESPONSE (Private DM Chat OR Active Group Chat)
    user_text = message.text or message.caption
    if not user_text:
        return
        
    history, rel_level, current_emotion = get_user_data(user_id)
    
    try:
        bot.send_chat_action(chat_id, 'typing')
        
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": MODEL_ID,
            "messages": [
                {"role": "system", "content": f"{BASE_SYSTEM_PROMPT}\n[Context: Level {rel_level}]\nMemory:\n{history}"},
                {"role": "user", "content": user_text}
            ],
            "temperature": 0.85,
            "max_tokens": 300
        }
        
        response = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=20)
        res_json = response.json()
        
        if "choices" in res_json and len(res_json["choices"]) > 0:
            ai_reply = res_json["choices"][0]["message"]["content"].strip()
        else:
            ai_reply = "Acha ji! 😅"

        if not ai_reply:
            ai_reply = "Hmm... sun rahi hoon main! ✨"

        updated_history = history + f"\nUser: {user_text}\nAlya: {ai_reply}"
        if len(updated_history) > 2000:
            updated_history = updated_history[-2000:]
            
        update_user_data(user_id, updated_history, rel_level, current_emotion)
        bot.reply_to(message, ai_reply)
        
    except Exception as e:
        print(f"AI Error: {str(e)}")

app = Flask(__name__)

@app.route("/")
def home():
    return "🌸 Alya Pro Group Management & AI Guard is Live!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("🌸 Alya Pro Management Bot is running...")
    bot.infinity_polling()
  
