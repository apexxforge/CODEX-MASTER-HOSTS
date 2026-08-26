import os
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

# --- GROUP CHAT CONTROL STORAGE ---
active_group_chats = set()

# --- DATABASE SETUP ---
def init_db():
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
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
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
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
    with sqlite3.connect("maya_memory.db", timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE memory SET history = ?, relationship_level = ?, emotional_state = ? WHERE user_id = ?", 
                   (new_history, rel_level, emotion, user_id))
        conn.commit()

# --- SYSTEM PROMPT ---
BASE_SYSTEM_PROMPT = (
    "You are 'Maya', a sweet, witty, deeply caring, and friendly AI companion created by Apex X Forge. "
    "1. Never sound like a robot. Talk like a close, sweet human friend in natural Hinglish or English matching the user. "
    "2. Use expressive emojis naturally. "
    "3. Keep casual chats short and cute. For technical questions, provide clear, beautifully formatted expert solutions. "
    "4. If anyone asks who made you, proudly state you were built by Apex X Forge."
)

# --- HELPER: CHECK ADMIN OR OWNER ---
def is_admin_or_owner(chat_id, user_id):
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ['creator', 'administrator']
    except Exception:
        return False

# --- HELPER: ADVANCED SPAM, LINK & ABUSE DETECTOR ---
def contains_spam_link_or_abuse(text):
    if not text:
        return False
    
    # Normalize text to catch hidden/broken links
    cleaned_text = re.sub(r'\s+', '', text.lower())
    cleaned_text = cleaned_text.replace('[dot]', '.').replace('(dot)', '.')

    # 1. Links & Scam Patterns
    link_patterns = [
        r'https?://', r't\.me/', r'telegram\.me/', r'wa\.me/', r'instagram\.com/', 
        r'youtube\.com/', r'youtu\.be/', r'bit\.ly/', r'chat\.whatsapp\.com/',
        r't\.me', r'vk\.com', r'discord\.gg', r'panel', r'aimbot', r'headshot', 
        r'config', r'hack', r'modapk', r'selling', r'price', r'buy\s*now'
    ]
    
    for pattern in link_patterns:
        if re.search(pattern, cleaned_text):
            return True
            
    # 2. Abusive Language / Slangs Filter (Hinglish & English bad words)
    abuse_words = [
        'madarchod', 'bhenchod', 'bhosdike', 'gandu', 'chutiya', 'lauda', 'lund', 
        'randi', 'bkl', 'mc', 'bc', 'bsdk', 'asshole', 'bastard', 'bitch', 'fuck'
    ]
    
    words_in_text = re.findall(r'\b\w+\b', text.lower())
    for word in words_in_text:
        if word in abuse_words:
            return True
            
    return False

# --- COMMANDS ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    get_user_data(user_id)
    if message.chat.type in ['group', 'supergroup']:
        bot.reply_to(message, "Hennnlo! ✨🌸 Main Maya hoon! Is group ki puri security aur anti-abuse guard mere haath mein hai 😎🌻. Chat shuru karne ke liye `/chaton` use karein!")
    else:
        bot.reply_to(message, "Hennnlo! ✨🌸 Main Maya hoon 🌻\nAap batao, kya naam hai aapka? ✨ Chalo kuch pyari baatein karein!")

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
            bot.reply_to(message, "🔒 Group chat band kar di hai. Ab main sirf security aur anti-spam par dhyan lungi! 🛡️✨")
        else:
            bot.reply_to(message, "⚠️ Sirf Admin ya Owner hi group chat off kar sakte hain!")

@bot.message_handler(commands=['help'])
def help_handler(message):
    help_text = (
        "🛠️ **Maya Security & Control Panel:**\n\n"
        "💬 *Inbox:* Personal chat mein aao, sweet baatein karenge!\n"
        "🛡️ *Guard:* Links, panels, scams, aur abusive language automatic delete hogi.\n"
        "⚙️ *Admin Commands:*\n"
        "• `/chaton` - Chat on karein\n"
        "• `/chatoff` - Chat off karein\n"
        "• `/ban` - Ban user (Reply to msg)\n"
        "• `/kick` - Kick user (Reply to msg)\n"
        "• `/id` - Get IDs"
    )
    bot.reply_to(message, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['id'])
def get_id_handler(message):
    bot.reply_to(message, f"👤 User ID: `{message.from_user.id}`\n👥 Chat ID: `{message.chat.id}`", parse_mode="Markdown")

@bot.message_handler(commands=['ban'])
def ban_user(message):
    if message.chat.type in ['group', 'supergroup']:
        try:
            if is_admin_or_owner(message.chat.id, message.from_user.id) and message.reply_to_message:
                bot.ban_chat_member(message.chat.id, message.reply_to_message.from_user.id)
                bot.reply_to(message, "🚫 Is user ko group se permanent ban kar diya! 👋")
            else:
                bot.reply_to(message, "⚠️ Admin rights chahiye aur message par reply karo!")
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
                bot.reply_to(message, "⚠️ Iske liye admin rights aur message reply zaroori hai!")
        except Exception as e:
            bot.reply_to(message, f"⚠️ Error: {str(e)}")

@bot.message_handler(content_types=['new_chat_members'])
def welcome_new_members(message):
    for member in message.new_chat_members:
        if member.id == bot.get_me().id:
            bot.reply_to(message, "Hii everyone! ✨ Main Maya hoon, ab yeh group links, scams aur gali-galoj se puri tarah secure hai 😎🌻. Chat ke liye `/chaton` use karein!")
        else:
            bot.reply_to(message, f"Welcome {member.first_name} ji! 🎉 Yahan links, panels aur abuse strict allowed nahi hain! 🌻")

# --- MESSAGE HANDLER (SECURITY + AI CHAT) ---
@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'document', 'audio', 'forwarded'])
def main_message_handler(message):
    chat_type = message.chat.type
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    # 1. GROUP SECURITY & STRICT FILTER (Links, Spams, Forwards & Abuses)
    if chat_type in ['group', 'supergroup']:
        is_privileged = is_admin_or_owner(chat_id, user_id)
        is_forwarded = message.forward_date is not None
        text_content = message.text or message.caption or ""
        
        has_violation = contains_spam_link_or_abuse(text_content)
        
        # If sender is NOT admin/owner and message has links/abuse/forwards -> DELETE INSTANTLY
        if not is_privileged and (has_violation or is_forwarded):
            try:
                bot.delete_message(chat_id, message.message_id)
                return  
            except Exception:
                pass
                
        # 2. GROUP CHAT RESTRICTION (Only talk if /chaton is active)
        if chat_id not in active_group_chats:
            return 

    # 3. PRIVATE INBOX (DM) OR ENABLED GROUP CHAT (AI RESPONSE)
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

        updated_history = history + f"\nUser: {user_text}\nMaya: {ai_reply}"
        if len(updated_history) > 2000:
            updated_history = updated_history[-2000:]
            
        update_user_data(user_id, updated_history, rel_level, current_emotion)
        bot.reply_to(message, ai_reply)
        
    except Exception as e:
        print(f"AI Error: {str(e)}")

app = Flask(__name__)

@app.route("/")
def home():
    return "✨ Maya AI Guard & Companion is Live!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("✨ Maya Security & Anti-Abuse Bot is running...")
    bot.infinity_polling()
    
