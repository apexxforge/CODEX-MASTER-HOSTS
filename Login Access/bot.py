import telebot
import json
import os
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

API_TOKEN = '8808432793:AAFRnOcMyE4vh8pcbZ_FQM0gWqCCjkk2p50'
BACKEND_DOMAIN = 'https://access-login-xwst.onrender.com'

bot = telebot.TeleBot(API_TOKEN)

user_states = {}
LINKS_FILE = "channels.txt"

def load_channels():
    """channels.txt se Telegram channels/groups load karta hai."""
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
            channels.append(line)

    return channels


def get_chat_identifier(channel):
    channel = channel.strip().rstrip("/")
    if channel.startswith("@"):
        return channel
    if "t.me/" in channel:
        username = channel.split("t.me/", 1)[1].split("?", 1)[0].split("/", 1)[0]
        return f"@{username}"
    return f"@{channel}"


def get_channel_link(channel):
    if channel.startswith(("http://", "https://")):
        return channel
    return f"https://t.me/{channel.replace('@', '')}"


def check_user_subscription(user_id):
    """Har listed channel/group ki membership verify karta hai."""
    channels = load_channels()
    not_joined = []

    for ch in channels:
        try:
            member = bot.get_chat_member(get_chat_identifier(ch), user_id)
            valid_statuses = ["creator", "administrator", "member"]
            is_restricted_member = (
                member.status == "restricted"
                and getattr(member, "is_member", False)
            )
            if member.status not in valid_statuses and not is_restricted_member:
                not_joined.append(ch)
        except Exception as e:
            print(f"Error checking chat {ch}: {e}")
            not_joined.append(ch)

    return not_joined


def get_channel_name(channel):
    return get_chat_identifier(channel).replace("@", "").replace("_", " ").replace("-", " ")


def get_main_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("🎮 Login Game"),
        KeyboardButton("💬 DM @ApexXForge")
    )
    return markup


def get_quick_menu():
    markup_inline = InlineKeyboardMarkup(row_width=2)
    markup_inline.add(
        InlineKeyboardButton("🎮 ʟᴏɢɪɴ ɢᴀᴍᴇ   ", callback_data="login_game"),
        InlineKeyboardButton("📢 ᴄʜᴀɴɴᴇʟs", url="https://t.me/ApexXchannels")
    )
    return markup_inline


def send_access_granted(chat_id):
    welcome_text = (
        "✅ **Access Granted**\n\n"
        "Welcome to Apex X Access🔥!\n"
        "You can now use all available features.\n\n"
        "💬 Need help? DM @ApexXForge"
    )

    bot.send_message(
        chat_id,
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )
    bot.send_message(
        chat_id,
        "🔥 **ACCESS PANEL**",
        reply_markup=get_quick_menu(),
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    channels = load_channels()

    if channels:
        not_joined = check_user_subscription(user_id)

        if not_joined:
            markup = InlineKeyboardMarkup(row_width=1)

            for ch in channels:
                markup.add(
                    InlineKeyboardButton(
                        f"📢 Join {get_channel_name(ch)}",
                        url=get_channel_link(ch)
                    )
                )

            markup.add(
                InlineKeyboardButton(
                    "✅ Verify Access",
                    callback_data="check_sub"
                )
            )

            sub_text = (
                "🔐 **Access Verification Required**\n\n"
                "To unlock all bot features, complete these steps:\n\n"
                "1️⃣ Join all required channels\n"
                "2️⃣ Return here after joining\n"
                "3️⃣ Tap **Verify Access**\n\n"
                "✨ Verification is automatic."
            )

            bot.send_message(
                message.chat.id,
                sub_text,
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return

    send_access_granted(message.chat.id)

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
            bot.answer_callback_query(call.id, "✅ Access Granted!")
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception:
                pass
            send_access_granted(chat_id)
        return

    # Security check for inline actions
    if channels := load_channels():
        if check_user_subscription(user_id):
            bot.answer_callback_query(call.id, "⚠️ Please verify your access first! Send /start", show_alert=True)
            return

    if call.data == "login_game":
        bot.answer_callback_query(call.id)
        user_states[chat_id] = "waiting_for_token"
        text = (
            "🔐 **SECURE LOGIN PANEL**\n\n"
            "💬 *Send your access token below to generate config.*\n\n"
            "⚠️ *Type `/cancel` to abort.*"
        )
        bot.send_message(chat_id, text, parse_mode="Markdown")
        
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

    if text == "💬 DM @ApexXForge":
        dm_markup = InlineKeyboardMarkup()
        dm_markup.add(
            InlineKeyboardButton("💬 Open @ApexXForge", url="https://t.me/ApexXForge")
        )
        bot.send_message(
            chat_id,
            "💬 **Support & Help**\n\nTap the button below to contact @ApexXForge.",
            reply_markup=dm_markup,
            parse_mode="Markdown"
        )
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

    if text == "🎮 Login Game":
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
        
        filename = "localconfig.json"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(json_string)
            
        guide_text = (
            f"📂 **ᴘʟᴀᴄᴇ ᴛʜɪs ғɪʟᴇ ɪɴ ʏᴏᴜʀ ɢᴀᴍᴇ ᴅɪʀᴇᴄᴛᴏʀʏ**\n\n"
            f"**ʜᴏᴡ ᴛᴏ ᴜsᴇ:**\n"
            f"1. ᴅᴏᴡɴʟᴏᴀᴅ ᴛʜᴇ ғɪʟᴇ\n"
            f"2. ᴏᴘᴇɴ ᴛʜᴇ ғᴏʟᴅᴇʀ ᴡʜᴇʀᴇ ᴛʜᴇ ғɪʟᴇ ᴡᴀs ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ\n"
            f"3. ᴄᴏᴘʏ ᴛʜᴇ ғɪʟᴇ\n"
            f"4. ɢᴏ ᴛᴏ:\n"
            f"`/storage/emulated/0/Android/data/com.dts.freefiremax/files/`\n"
            f"**or**\n"
            f"`/storage/emulated/0/Android/data/com.dts.freefireth/files/`\n"
            f"5. ᴘᴀsᴛᴇ ᴛʜᴇ ғɪʟᴇ ɪɴᴛᴏ ᴛʜɪs ғᴏʟᴅᴇʀ\n"
            f"6. ᴏᴘᴇɴ ꜰʀᴇᴇ ꜰɪʀᴇ ᴏʀ ꜰʀᴇᴇ ꜰɪʀᴇ ᴍᴀx, ᴄʜᴏᴏsᴇ ᴀɴʏ ᴘʟᴀᴛꜰᴏʀᴍ ᴀɴᴅ ʟᴏɢɪɴ ᴛᴏ ʏᴏᴜʀ ɢᴀᴍᴇ ᴀᴄᴄᴏᴜɴᴛ\n"
            f"7. ᴏɴᴄᴇ sᴜᴄᴄᴇssғᴜʟʟʏ ʟᴏɢɢᴇᴅ ɪɴ, ʏᴏᴜ ᴄᴀɴ ᴘʟᴀʏ ᴜɴʟɪᴍɪᴛᴇᴅ ᴍᴀᴛᴄʜᴇs\n\n"
            f"⚠️ **ɴᴏᴛᴇ:** ᴛʜɪs ᴍᴇᴛʜᴏᴅ ᴡᴏʀᴋs ᴡɪᴛʜ ʙᴏᴛʜ ꜰʀᴇᴇ ꜰɪʀᴇ ᴀɴᴅ ꜰʀᴇᴇ ꜰɪʀᴇ ᴍᴀx.\n"
            f"ɪғ ʏᴏᴜ ғᴀᴄᴇ ᴀɴʏ ɪssᴜᴇs, ᴅᴍ @ApexXForge"
        )
        
        with open(filename, "rb") as doc:
            bot.send_document(chat_id, doc, caption=guide_text, parse_mode="Markdown", reply_markup=get_main_keyboard())
            
        if os.path.exists(filename):
            os.remove(filename)
            
        del user_states[chat_id]

if __name__ == '__main__':
    print("Apex Bot is running successfully...")
    bot.infinity_polling()
    
