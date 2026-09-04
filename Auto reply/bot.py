import json
import os
import html
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ==========================================
# PASTE YOUR DETAILS HERE
# ==========================================
API_ID = 23531178
API_HASH = "c045a5e84f9f24b541eabc46256d2add"
SESSION_STRING = "1BVtsOLkBuy6DSCA8n_1pGjLcBy9OJp37JS4X3OVaSlCv2C74iKAbf1X8A-GbwNhC2bHpgbVbXWoUnpOPRaMuCp8UMmAtZ3N9SCJru_VfowfVQsDFu1LWf2QSj4hkYpn88U-Cns1GXApvgCUN1ue9o3gxsKQkox2_-4w984e6-ar7sU0HdcfCWgDLf6ygYJpn-JGbkG5FOPIpBPnW3STTzoqLBbB4DNrDguSfA3FKDk4w71GJsr1bYLnemnzyibcKpJZAtbC9-qloGwt96Z4vnuS0SxRP2Gf2i0keLoS0yqhXD_fBCOTIwY5ndLGUcR7JZcMmnmAD1aQZHrDNigqLW-BXW8A90JM="

DB_FILE = "replied_users.json"


def load_users():
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
    except Exception:
        pass
    return set()


def save_users(users):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(list(users), f)


replied_users = load_users()

client = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH
)


@client.on(events.NewMessage(incoming=True))
async def auto_reply(event):
    if not event.is_private:
        return

    sender = await event.get_sender()

    if not sender or getattr(sender, "bot", False):
        return

    user_id = sender.id

    # Reply only once to each user
    if user_id in replied_users:
        return

    name = html.escape(sender.first_name or "User")
    mention = f'<a href="tg://user?id={user_id}">{name}</a>'

    message = f"""👋 𝗛𝗲𝘆, {mention}!

🔥 𝗪𝗲𝗹𝗰𝗼𝗺𝗲 𝘁𝗼 𝗔𝗣𝗘𝗫 𝗫 𝗖𝗢𝗠𝗠𝗨𝗡𝗜𝗧𝗬 💎
Your official hub for tools, tutorials & bots.

╭─ 📚 𝗧𝗨𝗧𝗢𝗥𝗜𝗔𝗟𝗦
├ 🔐 <a href="https://youtu.be/vEmxuxdz0QI">𝗙𝗶𝗻𝗱 𝗦𝗲𝗰𝘂𝗿𝗶𝘁𝘆 𝗖𝗼𝗱𝗲</a>
├ 🔑 <a href="https://youtu.be/UYkmkzyzCIA">𝗔𝗰𝗰𝗲𝘀𝘀 𝗧𝗼𝗸𝗲𝗻 𝗟𝗼𝗴𝗶𝗻</a>
╰ ▶️ <a href="https://youtube.com/@apexxforge">𝗢𝗳𝗳𝗶𝗰𝗶𝗮𝗹 𝗬𝗼𝘂𝗧𝘂𝗯𝗲</a>

╭─ 📢 𝗔𝗣𝗘𝗫 𝗫 𝗡𝗘𝗧𝗪𝗢𝗥𝗞
├ 📣 <a href="https://t.me/ApexXchannels">𝗝𝗼𝗶𝗻 𝗢𝗳𝗳𝗶𝗰𝗶𝗮𝗹 𝗖𝗵𝗮𝗻𝗻𝗲𝗹</a>
╰ 💬 <a href="https://t.me/ApexXGroups">𝗝𝗼𝗶𝗻 𝗗𝗶𝘀𝗰𝘂𝘀𝘀𝗶𝗼𝗻 𝗚𝗿𝗼𝘂𝗽</a>

╭─ 🤖 𝗢𝗙𝗙𝗜𝗖𝗜𝗔𝗟 𝗕𝗢𝗧𝗦
├ 🔐 <a href="https://t.me/ApexXSecurityCodeBot">𝗦𝗲𝗰𝘂𝗿𝗶𝘁𝘆 𝗖𝗼𝗱𝗲 𝗕𝗼𝘁</a>
├ 🔑 <a href="https://t.me/ApexFFAccessTokenBot">𝗔𝗰𝗰𝗲𝘀𝘀 𝗧𝗼𝗸𝗲𝗻 𝗕𝗼𝘁</a>
├ 🛡️ <a href="https://t.me/BanPermanentFF_bot">𝗕𝗮𝗻 𝗦𝘁𝗮𝘁𝘂𝘀 𝗕𝗼𝘁</a>
├ 🤖 <a href="https://t.me/ApexXAllBot">𝗔𝗣𝗘𝗫 𝗫 𝗔𝗹𝗹-𝗜𝗻-𝗢𝗻𝗲</a>
╰ 📊 <a href="https://t.me/ApexFFInfoBot">𝗙𝗙 𝗜𝗻𝗳𝗼 𝗕𝗼𝘁</a>

💎 𝗔𝗣𝗘𝗫 𝗫 𝗙𝗢𝗥𝗚𝗘 • 𝗢𝗙𝗙𝗜𝗖𝗜𝗔𝗟"""

    await event.reply(message, parse_mode="html", link_preview=False)

    replied_users.add(user_id)
    save_users(replied_users)

    print(f"✅ Replied: {sender.first_name} | {user_id}")


print("🔥 APEX X FORGE AUTO REPLY STARTED")
print("📩 Waiting for private messages...")

client.start()
client.run_until_disconnected()
