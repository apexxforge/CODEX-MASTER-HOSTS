import telebot
import json
import os
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

API_TOKEN = '8808432793:AAFRnOcMyE4vh8pcbZ_FQM0gWqCCjkk2p50'
BACKEND_DOMAIN = 'https://access-login-xwst.onrender.com'

bot = telebot.TeleBot(API_TOKEN)

user_states = {}
LINKS_FILE = "links.txt"

def load_channels():
    """links.txt file se sirf Telegram channels load karne ke liye"""
    channels = []
    if not os.path.exists(LINKS_FILE):
        default_content = (
            "@ApexXForge\n"
            "@ApexXAllBot\n"
            "@ApexHUBcodex"
        )
        with open(LINKS_FILE, "w", encoding="utf-8") as f:
            f.write(default_content)
    
    with open(LINKS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "youtube.com" not in line and "youtu.be" not in line:
                channels.append(line)
                
    return channels

def check_user_subscription(user_id):
    """Check karta hai ki user ne channels/groups join kiye hain ya nahi"""
    channels = load_channels()
    not_joined = []
    
    for ch in channels:
        chat_identifier = ch if ch.startswith("@") else f"@{ch.split('/')[-1]}"
        try:
            member = bot.get_chat_member(chat_identifier, user_id)
            if member.status in ['left', 'kicked']:
                not_joined.append(ch)
        except Exception as e:
            print(f"Error checking chat {chat_identifier}: {e}")
            pass
            
    return not_joined

def get_main_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("⚡ Create Token File"),
        KeyboardButton("📊 Check Status"),
        KeyboardButton("🔑 Get Token"),
        KeyboardButton("🟢 Status"),
        KeyboardButton("📜 History"),
        KeyboardButton("❓ Help")
    )
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    channels = load_channels()
    
    # Force subscription check with your exact requested style
    if channels:
        not_joined = check_user_subscription(user_id)
        if not_joined:
            markup = InlineKeyboardMarkup(row_width=1)
            for ch in channels:
                link_url = ch if ch.startswith("http") else f"https://t.me/{ch.replace('@', '')}"
                # Channel name clean karne ke liye (jaise @ApexXForge se 'Apex X Forge' banana)
                ch_name = ch.replace('@', '').replace('X', ' X ')
                markup.add(InlineKeyboardButton(f"📢 Join {ch_name}", url=link_url))
                
            markup.add(InlineKeyboardButton("✅ Verify Access", callback_data="check_sub"))
            
            sub_text = (
                "👋 **Welcome to Apex X Access❤️**\n\n"
                "Complete the required steps below\n"
                "to unlock bot access.\n\n"
                "• Join all official channels\n"
                "• Click Verify Access to continue"
            )
            bot.send_message(message.chat.id, sub_text, reply_markup=markup, parse_mode="Markdown")
            return

    # Successful Verification / Welcome Message
    markup_inline = InlineKeyboardMarkup(row_width=2)
    markup_inline.add(
        InlineKeyboardButton("🎮 LOGIN GAME", callback_data="login_game"),
        InlineKeyboardButton("📊 SYSTEM STATUS", callback_data="status"),
        InlineKeyboardButton("⏹ TERMINATE", callback_data="stop"),
        InlineKeyboardButton("❓ HELP DESK", callback_data="help")
    )
    
    welcome_text = (
        "✅ **Access Granted**\n\n"
        "Welcome to Apex X Access🔥!\n"
        "You can now use all available features."
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")
    bot.send_message(message.chat.id, "❤️ **Quick Menu:**", reply_markup=markup_inline, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    
    if call.data == "check_sub":
        channels = load_channels()
        not_joined = check_user_subscription(user_id)
        if not_joined:
            fail_text = (
                "🔒 **Access Locked**\n\n"
                "Please join all required channels\n"
                "and verify your access to continue."
            )
            bot.answer_callback_query(call.id, "❌ Please join all channels first!", show_alert=True)
            return
        else:
            bot.answer_callback_query(call.id, "✅ ❤️Access Granted!")
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except:
                pass
            fake_message = call.message
            fake_message.from_user.id = user_id
            send_welcome(fake_message)
        return

    # Security check for inline actions
    if channels := load_channels():
        if check_user_subscription(user_id):
            bot.answer_callback_query(call.id, "⚠️ Please verify your access first! Send /start", show_alert=True)
            return

    if call.data == "login_game":
        user_states[chat_id] = "waiting_for_token"
        text = (
            "🔐 **SECURE LOGIN PANEL**\n\n"
            "💬 *Send your access token below to generate config.*\n\n"
            "⚠️ *Type `/cancel` to abort.*"
        )
        bot.send_message(chat_id, text, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        
    elif call.data == "status":
        status_text = (
            "📊 **SYSTEM STATUS**\n\n"
            "🚀 Server: `Online [Stable]`\n"
            "⚡ Speed: `Optimal`"
        )
        bot.send_message(chat_id, status_text, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        
    elif call.data == "stop":
        if chat_id in user_states:
            del user_states[chat_id]
        stop_text = "⏹ **Session Terminated Successfully.**"
        bot.send_message(chat_id, stop_text, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        
    elif call.data == "help":
        help_text = (
            "❓ **QUICK HELP**\n\n"
            "1️⃣ Tap **LOGIN GAME**\n"
            "2️⃣ Send Access Token\n"
            "3️⃣ Download & apply `localconfig.json`\n\n"
            "💬 Support: `@ApexXForge`"
        )
        bot.send_message(chat_id, help_text, parse_mode="Markdown")
        bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == '/cancel':
        if chat_id in user_states:
            del user_states[chat_id]
        bot.send_message(chat_id, "❌ **Session Aborted.**", parse_mode="Markdown", reply_markup=get_main_keyboard())
        return

    # Check subscription for all text inputs
    if channels := load_channels():
        if check_user_subscription(user_id):
            locked_msg = (
                "🔒 **Access Locked**\n\n"
                "Please join all required channels\n"
                "and verify your access to continue. Send `/start`"
            )
            bot.send_message(chat_id, locked_msg, parse_mode="Markdown")
            return

    if text in ["⚡ Create Token File", "🔑 Get Token"]:
        user_states[chat_id] = "waiting_for_token"
        msg = (
            "🔐 **SECURE LOGIN PANEL**\n\n"
            "💬 *Send your access token below to generate config.*"
        )
        bot.send_message(chat_id, msg, parse_mode="Markdown", reply_markup=get_main_keyboard())
        return

    elif text in ["📊 Check Status", "🟢 Status"]:
        status_text = (
            "📊 **SYSTEM STATUS**\n\n"
            "🚀 Server: `Online [Stable]`\n"
            "⚡ Speed: `Optimal`"
        )
        bot.send_message(chat_id, status_text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        return

    elif text == "📜 History":
        history_text = "📜 **History:** No active sessions stored."
        bot.send_message(chat_id, history_text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        return

    elif text == "❓ Help":
        help_text = (
            "❓ **QUICK HELP**\n\n"
            "1️⃣ Tap **Create Token File**\n"
            "2️⃣ Send Access Token\n"
            "3️⃣ Use `localconfig.json` in game folder\n\n"
            "💬 Support: `@ApexXForge`"
        )
        bot.send_message(chat_id, help_text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        return

    # Token processing and file generation logic
    if user_states.get(chat_id) == "waiting_for_token":
        token = text
        bot.send_message(chat_id, "⏳ *Generating config file...*", parse_mode="Markdown")

        server_url = f"{BACKEND_DOMAIN}/{token}/"
        
        config_data = {
            "serverUrl": server_url
        }
        
        json_string = json.dumps(config_data, indent=2)
        
        filename = f"localconfig_{chat_id}.json"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(json_string)
            
        guide_text = (
            f"✅ **CONFIG GENERATED SUCCESSFULLY**\n\n"
            f"📂 **Path:**\n"
            f"`/storage/emulated/0/Android/data/com.dts.freefireth/files/`\n"
            f"*(MAX: `.../com.dts.freefiremax/files/`)*\n\n"
            f"📋 **Code Preview:**\n"
            f"```json\n{json_string}\n```\n\n"
            f"🌐 Hub: `@ApexXAllBot` | 💬 Support: `@ApexXForge`"
        )
        
        with open(filename, "rb") as doc:
            bot.send_document(chat_id, doc, caption=guide_text, parse_mode="Markdown", reply_markup=get_main_keyboard())
            
        if os.path.exists(filename):
            os.remove(filename)
            
        del user_states[chat_id]

if __name__ == '__main__':
    print("Apex Bot is running successfully...")
    bot.infinity_polling()
    
