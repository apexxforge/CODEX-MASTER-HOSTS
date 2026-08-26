import os
import sys
import json
import requests
import urllib.parse
import urllib3
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# Safe import for Protobuf files
try:
    import MajorLoginReq_pb2
except ImportError:
    try:
        import MajoRLogin_pb2_2 as MajorLoginReq_pb2
    except ImportError:
        MajorLoginReq_pb2 = None

try:
    import MajorLoginRes_pb2
except ImportError:
    try:
        import MajorLoginRes_pb2_2 as MajorLoginRes_pb2
    except ImportError:
        MajorLoginRes_pb2 = None

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOKEN = "8496330355:AAHcdBm3DxaLzeJDa2B_z5icpvzdWVP2JqI"
router = Router()

def convert_seconds(s):
    d, h = divmod(s, 86400)
    h, m = divmod(h, 3600)
    m, s = divmod(m, 60)
    return f"{d} Days, {h} Hrs, {m} Mins"

def fetch_player_full_profile(token):
    try:
        support_url = f"https://api-otrss.garena.com/support/callback/?access_token={token}"
        support_res = requests.get(support_url, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True, timeout=15)
        parsed_url = urllib.parse.urlparse(support_res.url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        
        uid = query_params.get("account_id", ["Unknown"])[0]
        nickname = urllib.parse.unquote(query_params.get("nickname", ["N/A"])[0])
        region = query_params.get("region", ["Unknown"])[0]

        bind_url = "https://100067.connect.garena.com/game/account_security/bind:get_bind_info"
        payload = {"app_id": "100067", "access_token": token}
        headers = {"User-Agent": "GarenaMSDK/4.0.19P9"}
        bind_res = requests.get(bind_url, params=payload, headers=headers, timeout=10, verify=False)
        
        curr_email, pend_email, countdown = "None", "None", 0
        if bind_res.status_code == 200:
            bind_data = bind_res.json()
            curr_email = bind_data.get("email") or "None"
            pend_email = bind_data.get("email_to_be") or "None"
            countdown = bind_data.get("request_exec_countdown", 0)

        return {
            "uid": uid,
            "nickname": nickname,
            "region": region,
            "current_email": curr_email,
            "pending_email": pend_email,
            "countdown": countdown
        }
    except Exception:
        return None

def main_menu_inline():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Add Recovery Email", callback_data="menu_bind_email"),
                InlineKeyboardButton(text="🔍 Check Bind Profile", callback_data="menu_check_bind"),
            ],
            [
                InlineKeyboardButton(text="🔄 Change Rebind Email", callback_data="menu_change_email"),
                InlineKeyboardButton(text="🔓 Unbind Account Request", callback_data="menu_unbind_email"),
            ],
            [
                InlineKeyboardButton(text="❌ Cancel Pending Request", callback_data="menu_cancel_bind"),
                InlineKeyboardButton(text="📱 Bound Platforms", callback_data="menu_bound_platforms"),
            ],
            [
                InlineKeyboardButton(text="📊 Get Player & UID Info", callback_data="menu_token_info"),
                InlineKeyboardButton(text="🛑 Revoke Access Token", callback_data="menu_revoke_token"),
            ]
        ]
    )

class FlowStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_email = State()
    waiting_for_otp = State()
    waiting_for_security_code = State()

class GeneralStates(StatesGroup):
    waiting_for_input = State()
    action_type = State()

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "<b>🛡️ APEX X SECURITY SUITE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Welcome! Select an operation from the options below:</i>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu_inline())

@router.callback_query(F.data == "menu_home")
async def cb_home(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text = (
        "<b>🛡️ APEX X SECURITY SUITE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Main Menu Options:</i>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_inline())
    await callback.answer()

@router.callback_query(F.data.in_({
    "menu_check_bind", "menu_cancel_bind", "menu_unbind_email", 
    "menu_change_email", "menu_bound_platforms", "menu_token_info", "menu_revoke_token"
}))
async def handle_menu(callback: types.CallbackQuery, state: FSMContext):
    prompts = {
        "menu_check_bind": ("check_bind", "🔍 <b>Check Bind & Player Profile</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Send your Garena Access Token below:</i>"),
        "menu_cancel_bind": ("cancel_bind", "❌ <b>Cancel Pending Request</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Send your Garena Access Token below:</i>"),
        "menu_unbind_email": ("unbind_email", "🔓 <b>Unbind Account Email</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Send your Garena Access Token below:</i>"),
        "menu_change_email": ("change_email", "🔄 <b>Change Bind Email</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Send your Garena Access Token below:</i>"),
        "menu_bound_platforms": ("bound_platforms", "📱 <b>Check Bound Platforms</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Send your Garena Access Token below:</i>"),
        "menu_token_info": ("token_info", "📊 <b>Get Player Profile & UID</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Send your Garena Access Token below:</i>"),
        "menu_revoke_token": ("revoke_token", "🛑 <b>Revoke Access Token</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<i>Send your Garena Access Token below:</i>")
    }
    action, text = prompts.get(callback.data)
    await state.set_state(GeneralStates.waiting_for_input)
    await state.update_data(action_type=action)
    
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« Back to Main Menu", callback_data="menu_home")]])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb)
    await callback.answer()

@router.callback_query(F.data == "menu_bind_email")
async def cb_add_email(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(FlowStates.waiting_for_token)
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« Back to Main Menu", callback_data="menu_home")]])
    text = (
        "<b>➕ Add Recovery Email [1/3]</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Please send your Garena Access Token:</i>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb)
    await callback.answer()

@router.message(FlowStates.waiting_for_token)
async def flow_token(message: Message, state: FSMContext):
    await state.update_data(access_token=message.text.strip())
    await state.set_state(FlowStates.waiting_for_email)
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« Back to Main Menu", callback_data="menu_home")]])
    await message.answer("📧 <b>Enter Target Email Address:</b>\n━━━━━━━━━━━━━━━━━━━━━━", parse_mode="HTML", reply_markup=back_kb)

@router.message(FlowStates.waiting_for_email)
async def flow_email(message: Message, state: FSMContext):
    email = message.text.strip()
    await state.update_data(email=email)
    data = await state.get_data()
    
    url = "https://100067.connect.garena.com/game/account_security/bind:send_otp"
    payload = {"email": email, "locale": "en_US", "region": "SG", "app_id": "100067", "access_token": data["access_token"]}
    headers = {"User-Agent": "GarenaMSDK/4.0.30", "Content-Type": "application/x-www-form-urlencoded"}
    
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« Back to Main Menu", callback_data="menu_home")]])
    try:
        resp = requests.post(url, headers=headers, data=payload, timeout=15, verify=False)
        if resp.json().get("result", -1) == 0:
            await message.answer(
                f"✅ <b>OTP Dispatched Successfully!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Sent to: <code>{email}</code>\n"
                f"<i>Please reply with the verification code:</i>", 
                parse_mode="HTML",
                reply_markup=back_kb
            )
            await state.set_state(FlowStates.waiting_for_otp)
        else:
            await message.answer(f"❌ <b>Request Failed:</b>\n<pre>{resp.text}</pre>", parse_mode="HTML", reply_markup=back_kb)
            await state.clear()
    except Exception as e:
        await message.answer(f"❌ <b>Error:</b> <code>{str(e)}</code>", parse_mode="HTML", reply_markup=back_kb)
        await state.clear()

@router.message(FlowStates.waiting_for_otp)
async def flow_otp(message: Message, state: FSMContext):
    otp = message.text.strip()
    data = await state.get_data()
    url = "https://100067.connect.garena.com/game/account_security/bind:verify_otp"
    payload = {"app_id": "100067", "access_token": data["access_token"], "email": data["email"], "code": otp, "otp": otp, "type": "1"}
    headers = {"User-Agent": "GarenaMSDK/4.0.30", "Content-Type": "application/x-www-form-urlencoded"}
    
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« Back to Main Menu", callback_data="menu_home")]])
    try:
        resp = requests.post(url, headers=headers, data=payload, timeout=15, verify=False)
        res_json = resp.json()
        verifier = res_json.get("verifier_token", "")
        if res_json.get("result", -1) != 0 or not verifier:
            await message.answer(f"❌ <b>Invalid OTP Code:</b>\n<pre>{resp.text}</pre>", parse_mode="HTML", reply_markup=back_kb)
            await state.clear()
            return
        await state.update_data(verifier_token=verifier)
        await state.set_state(FlowStates.waiting_for_security_code)
        await message.answer(
            "🔐 <b>OTP Verified!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Now enter your 6-digit secondary security password:</i>", 
            parse_mode="HTML",
            reply_markup=back_kb
        )
    except Exception as e:
        await message.answer(f"❌ <b>Error:</b> <code>{str(e)}</code>", parse_mode="HTML", reply_markup=back_kb)
        await state.clear()

@router.message(FlowStates.waiting_for_security_code)
async def flow_final(message: Message, state: FSMContext):
    sec_code = message.text.strip()
    data = await state.get_data()
    url = "https://100067.connect.garena.com/game/account_security/bind:create_bind_request"
    payload = {"email": data["email"], "app_id": "100067", "access_token": data["access_token"], "verifier_token": data["verifier_token"], "secondary_password": sec_code}
    headers = {"User-Agent": "GarenaMSDK/4.0.30", "Content-Type": "application/x-www-form-urlencoded"}
    
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« Back to Main Menu", callback_data="menu_home")]])
    try:
        resp = requests.post(url, headers=headers, data=payload, timeout=15, verify=False)
        await message.answer(
            f"🏁 <b>Bind Process Completed</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<pre>{resp.text}</pre>", 
            parse_mode="HTML", 
            reply_markup=back_kb
        )
    except Exception as e:
        await message.answer(f"❌ <b>Error:</b> <code>{str(e)}</code>", parse_mode="HTML", reply_markup=back_kb)
    await state.clear()

@router.message(GeneralStates.waiting_for_input)
async def handle_general(message: Message, state: FSMContext):
    user_data = await state.get_data()
    action = user_data.get("action_type")
    token = message.text.strip()
    headers = {"User-Agent": "GarenaMSDK/4.0.30", "Content-Type": "application/x-www-form-urlencoded"}
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« Back to Main Menu", callback_data="menu_home")]])

    if action in ["check_bind", "token_info"]:
        try:
            profile = fetch_player_full_profile(token)
            if profile:
                response_text = (
                    "📊 <b>PLAYER & ACCOUNT PROFILE</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Nickname:</b> {profile['nickname']}\n"
                    f"🆔 <b>UID:</b> <code>{profile['uid']}</code>\n"
                    f"🌐 <b>Region:</b> {profile['region']}\n"
                    f"📧 <b>Current Email:</b> <code>{profile['current_email']}</code>\n"
                    f"⏳ <b>Pending Email:</b> <code>{profile['pending_email']}</code>\n"
                )
                if profile['countdown'] > 0:
                    response_text += f"⏰ <b>Countdown:</b> <code>{convert_seconds(profile['countdown'])}</code>\n"
                
                await message.answer(response_text, parse_mode="HTML", reply_markup=back_kb)
            else:
                await message.answer("❌ <b>Failed to fetch data.</b> Please check if your token is valid.", parse_mode="HTML", reply_markup=back_kb)
        except Exception as e:
            await message.answer(f"❌ <b>Error:</b> <code>{str(e)}</code>", parse_mode="HTML", reply_markup=back_kb)

    elif action == "cancel_bind":
        url = "https://100067.connect.garena.com/game/account_security/bind:cancel_request"
        resp = requests.post(url, headers=headers, data={"app_id": "100067", "access_token": token}, verify=False)
        await message.answer(f"🏁 <b>Cancel Request Result:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{resp.text}</pre>", parse_mode="HTML", reply_markup=back_kb)

    elif action == "unbind_email":
        url = "https://100067.connect.garena.com/game/account_security/bind:create_unbind_request"
        resp = requests.post(url, headers=headers, data={"app_id": "100067", "access_token": token}, verify=False)
        await message.answer(f"🔓 <b>Unbind Request Result:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{resp.text}</pre>", parse_mode="HTML", reply_markup=back_kb)

    elif action == "change_email":
        url = "https://100067.connect.garena.com/game/account_security/bind:create_rebind_request"
        resp = requests.post(url, headers=headers, data={"app_id": "100067", "access_token": token}, verify=False)
        await message.answer(f"🔄 <b>Change Rebind Result:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{resp.text}</pre>", parse_mode="HTML", reply_markup=back_kb)

    elif action == "bound_platforms":
        url = "https://100067.connect.garena.com/bind/app/platform/info/get"
        resp = requests.get(url, params={"app_id": "100067", "access_token": token}, headers=headers, verify=False)
        await message.answer(f"📱 <b>Bound Platforms Result:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{resp.text}</pre>", parse_mode="HTML", reply_markup=back_kb)

    elif action == "revoke_token":
        url = "https://100067.connect.garena.com/oauth/logout"
        resp = requests.post(url, headers=headers, data={"access_token": token}, verify=False)
        await message.answer(f"🛑 <b>Token Revocation Result:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{resp.text}</pre>", parse_mode="HTML", reply_markup=back_kb)

    await state.clear()

async def main():
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    await bot.delete_webhook(drop_pending_updates=True)
    print("[*] Bot successfully started polling with new token...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("[!] Bot stopped.")
    
