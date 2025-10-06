
import asyncio
import logging
import random
import re
import string
import requests
import csv
import io
import os
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ==============================================================================
# CONFIG
# ==============================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")   # from Replit secrets

# Admin
ADMIN_ID = 5749756239
USERS_PER_PAGE = 5

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Reply keyboard
REPLY_MARKUP = ReplyKeyboardMarkup(
    [["🔐 2FA Authenticator", "📧 Temp Mail Service"],
     ["🔍 Facebook Checker"]],
    resize_keyboard=True
)

# User data storage
user_data = {}

# ==============================================================================
# OTP / 2FA
# ==============================================================================
def calculate_totp(secret_key: str):
    try:
        code = str(random.randint(100000, 999999))
        remaining = 30
        return code, remaining
    except Exception:
        return None, 0

def format_countdown_message(code: str, remaining: int) -> str:
    return f"🔑 <b>{code}</b>\n⏳ Refreshes in {remaining}s"

def get_otp_inline_markup(secret_key: str):
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ CLAIMED 🧧", callback_data="claim_otp")]])

async def stop_active_otp_job(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    jobs = context.job_queue.get_jobs_by_name(f"otp_countdown_{chat_id}")
    for job in jobs:
        job.schedule_removal()

async def countdown_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    message_id = job.data["message_id"]
    secret_key = job.data["secret_key"]
    code, remaining = calculate_totp(secret_key)
    if not code:
        return
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=format_countdown_message(code, remaining),
            reply_markup=get_otp_inline_markup(secret_key),
            parse_mode="HTML"
        )
    except Exception:
        pass

async def start_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE, secret_key: str):
    chat_id = update.effective_chat.id
    await stop_active_otp_job(chat_id, context)
    code, remaining = calculate_totp(secret_key)
    if not code:
        await update.message.reply_text("⚠️ Invalid secret key.", parse_mode="HTML")
        return
    msg = await update.message.reply_text(format_countdown_message(code, remaining),
                                          reply_markup=get_otp_inline_markup(secret_key),
                                          parse_mode="HTML")
    job_data = {"chat_id": chat_id, "message_id": msg.message_id, "secret_key": secret_key}
    context.job_queue.run_repeating(countdown_job, interval=1, first=1, data=job_data, name=f"otp_countdown_{chat_id}")

async def claim_otp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    await stop_active_otp_job(chat_id, context)
    await query.edit_message_text("✅ <b>OTP CLAIMED!</b>", parse_mode="HTML")

# ==============================================================================
# Temp Mail
# ==============================================================================
def initialize_user_data(chat_id):
    if chat_id not in user_data:
        user_data[chat_id] = {"emails": [], "active": None, "last_seen_id": None, "username": None, "auto_gen_on": False}

def generate_random_name(min_len=6, max_len=12):
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(random.randint(min_len, max_len)))

def generate_email(username_prefix=None):
    name = username_prefix if username_prefix and username_prefix.isalnum() else generate_random_name()
    return f"{name}@mailto.plus"

async def generate_new_email_logic(chat_id: int, username_prefix: str, update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback: bool):
    await stop_active_otp_job(chat_id, context)
    email = generate_email(username_prefix)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_data[chat_id]["emails"].append({"address": email, "created": created_at})
    user_data[chat_id]["active"] = email
    user_data[chat_id]["last_seen_id"] = None
    response_text = f"〽️New Web Mail Generated:\n`{email}`"
    if is_callback:
        await update.callback_query.edit_message_text(response_text, parse_mode="Markdown",
                                                      reply_markup=get_tempmail_inline_markup(is_admin=(chat_id == ADMIN_ID)))
    else:
        await update.message.reply_text(response_text, parse_mode="Markdown",
                                        reply_markup=get_tempmail_inline_markup(is_admin=(chat_id == ADMIN_ID)))

def get_tempmail_inline_markup(is_admin=False):
    buttons = [
        [InlineKeyboardButton("📧 Generate", callback_data="generate"),
         InlineKeyboardButton("📊 Admin Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("♻️ Auto gen", callback_data="auto_gen_inline"),
         InlineKeyboardButton("✍️ Set Username", callback_data="set_username_inline")]
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("👤 See User Info", callback_data="see_users_0")])
        buttons.append([InlineKeyboardButton("📤 Export All Users", callback_data="export_all")])
    return InlineKeyboardMarkup(buttons)

# Temp Mail Handlers
async def generate_email_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    initialize_user_data(chat_id)
    await generate_new_email_logic(chat_id, user_data[chat_id].get("username"), update, context, is_callback=True)

async def auto_gen_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    initialize_user_data(chat_id)
    user_data[chat_id]["auto_gen_on"] = not user_data[chat_id]["auto_gen_on"]
    status = "ON ✅" if user_data[chat_id]["auto_gen_on"] else "OFF ❌"
    await query.answer(f"Auto gen is now {status}", show_alert=True)
    await query.edit_message_reply_markup(get_tempmail_inline_markup(is_admin=(chat_id == ADMIN_ID)))

async def set_username_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    initialize_user_data(chat_id)
    await query.message.reply_text("✍️ Please type your desired username (alphanumeric only).")
    context.user_data["awaiting_username"] = True

# ==============================================================================
# Admin Functions
# ==============================================================================
async def admin_see_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.message.chat_id != ADMIN_ID:
        await query.answer("⛔ Not authorized!", show_alert=True)
        return
    page = int(query.data.split("_")[2]) if "_" in query.data else 0
    all_users = list(user_data.keys())
    total_users = len(all_users)
    if not total_users:
        await query.edit_message_text("📭 No users yet.")
        return
    start, end = page * USERS_PER_PAGE, (page + 1) * USERS_PER_PAGE
    page_users = all_users[start:end]
    buttons = []
    for uid in page_users:
        total = len(user_data[uid].get("emails", []))
        buttons.append([InlineKeyboardButton(f"User {uid} ({total} mails)", callback_data=f"user_{uid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⏮️ Back", callback_data=f"see_users_{page-1}"))
    if end < total_users:
        nav.append(InlineKeyboardButton("Next ⏭️", callback_data=f"see_users_{page+1}"))
    if nav:
        buttons.append(nav)
    await query.edit_message_text(f"👤 **User List (Page {page+1})**\nTotal: {total_users}",
                                  reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def admin_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.message.chat_id != ADMIN_ID:
        await query.answer("⛔ Not authorized!", show_alert=True)
        return
    target_id = int(query.data.split("_", 1)[1])
    data = user_data.get(target_id)
    if not data:
        await query.edit_message_text("❌ No data for this user.")
        return
    emails = data.get("emails", [])
    msg = f"👤 User `{target_id}`\n📧 Total: {len(emails)}\n✅ Active: {data.get('active')}\n\n📜 History:\n"
    if emails:
        msg += "\n".join([f"• `{e['address']}` (🕒 {e['created']})" for e in emails])
    else:
        msg += "No mails."
    buttons = [[InlineKeyboardButton("📤 Export This User", callback_data=f"export_user_{target_id}")],
               [InlineKeyboardButton("⬅️ Back", callback_data="see_users_0")]]
    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def admin_export_single_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    target_id = int(query.data.split("_", 2)[2])
    data = user_data.get(target_id)
    if not data:
        await query.answer("❌ No user data", show_alert=True)
        return
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["User ID", "Email", "Created At"])
    for e in data["emails"]:
        writer.writerow([target_id, e["address"], e["created"]])
    output.seek(0)
    csv_file = io.BytesIO(output.getvalue().encode())
    csv_file.name = f"user_{target_id}.csv"
    await context.bot.send_document(chat_id=ADMIN_ID, document=csv_file, caption=f"📤 User {target_id} export")

async def admin_export_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not user_data:
        await query.edit_message_text("📭 No user data.")
        return
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["User ID", "Email", "Created At"])
    for uid, data in user_data.items():
        for e in data["emails"]:
            writer.writerow([uid, e["address"], e["created"]])
    output.seek(0)
    csv_file = io.BytesIO(output.getvalue().encode())
    csv_file.name = "user_data.csv"
    await context.bot.send_document(chat_id=ADMIN_ID, document=csv_file, caption="📤 All users export")

# ==============================================================================
# Facebook Checker
# ==============================================================================
FB_CHECKER_MARKUP = ReplyKeyboardMarkup(
    [["✅ Enable Friends Check", "❌ Disable Friends Check"], ["⬅️ Back"]],
    resize_keyboard=True
)

async def facebook_checker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["check_friends"] = False
    context.user_data["awaiting_fb_ids"] = True
    await update.message.reply_text(
        "🔍 Facebook Checker Mode\n\nSend me one or more Facebook IDs (separated by commas, spaces, or newlines).\nYou can also enable/disable 'Check Friends'.",
        reply_markup=FB_CHECKER_MARKUP
    )

async def toggle_friends_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "✅ Enable Friends Check":
        context.user_data["check_friends"] = True
        await update.message.reply_text("✅ Friends check enabled.")
    elif text == "❌ Disable Friends Check":
        context.user_data["check_friends"] = False
        await update.message.reply_text("❌ Friends check disabled.")
    elif text == "⬅️ Back":
        context.user_data["awaiting_fb_ids"] = False
        await update.message.reply_text("↩️ Back to main menu.", reply_markup=REPLY_MARKUP)

async def handle_facebook_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_fb_ids"):
        return
    raw_text = update.message.text.strip()
    ids = re.split(r"[,\s]+", raw_text)
    ids = [i for i in ids if i.isdigit()]
    if not ids:
        await update.message.reply_text("⚠️ Please send valid numeric Facebook IDs.")
        return
    payload = {"inputData": ids, "checkFriends": context.user_data.get("check_friends", False), "userLang": "en"}
    try:
        res = requests.post("https://check.fb.tools/api/check/account", json=payload, timeout=15)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        await update.message.reply_text(f"❌ API error: {e}")
        return
    active_list, dead_list = [], []
    for entry in data.get("data", []):
        uid = entry.get("uid")
        status = entry.get("status", {}).get("name")
        if status == "valid":
            active_list.append(uid)
        else:
            dead_list.append(uid)
    summary = ("📌 <b>Status Report</b>\n\n"
               f"✅ <b>Total Active Accounts:</b> {len(active_list)}\n" + ("\n".join(active_list) if active_list else "—") + "\n\n"
               f"❌ <b>Total Dead Accounts:</b> {len(dead_list)}\n" + ("\n".join(dead_list) if dead_list else "—"))
    await update.message.reply_text(summary, parse_mode="HTML")
    txt_output = "Active Accounts:\n" + "\n".join(active_list) + "\n\nDead Accounts:\n" + "\n".join(dead_list)
    txt_file = io.BytesIO(txt_output.encode())
    txt_file.name = "facebook_check.txt"
    await context.bot.send_document(chat_id=update.effective_chat.id, document=txt_file, caption="📤 Facebook Check Results")

# ==============================================================================
# Bot Commands
# ==============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    initialize_user_data(chat_id)
    await stop_active_otp_job(chat_id, context)
    await update.message.reply_text("👋 Welcome!\n\nChoose a service:", reply_markup=REPLY_MARKUP)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    initialize_user_data(chat_id)

    # Username setting
    if context.user_data.get("awaiting_username"):
        if text.isalnum():
            user_data[chat_id]["username"] = text
            context.user_data["awaiting_username"] = False
            await update.message.reply_text(f"✅ Username set to: {text}", reply_markup=get_tempmail_inline_markup(is_admin=(chat_id == ADMIN_ID)))
        else:
            await update.message.reply_text("⚠️ Username must be alphanumeric only. Try again.")
        return

    # Facebook checker
    if context.user_data.get("awaiting_fb_ids", False):
        await handle_facebook_ids(update, context)
        return

    if text == "📧 Temp Mail Service":
        await update.message.reply_text("Welcome to Temp Mail!", reply_markup=get_tempmail_inline_markup(is_admin=(chat_id == ADMIN_ID)))
    elif text == "🔐 2FA Authenticator":
        await update.message.reply_text("Send your 2FA secret key.", parse_mode="HTML")
    elif text == "🔍 Facebook Checker":
        await facebook_checker_handler(update, context)
    else:
        cleaned_key = text.replace(" ", "").upper()
        code, _ = calculate_totp(cleaned_key)
        if code:
            await start_countdown(update, context, cleaned_key)
        else:
            await update.message.reply_text("⚠️ Invalid input.")

# ==============================================================================
# Main Runner
# ==============================================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(claim_otp_handler, pattern="^claim_otp$"))
    app.add_handler(CallbackQueryHandler(admin_see_users, pattern="^see_users_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_user_details, pattern="^user_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_export_single_user, pattern="^export_user_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_export_all, pattern="^export_all$"))
    app.add_handler(CallbackQueryHandler(generate_email_button, pattern="^generate$"))
    app.add_handler(CallbackQueryHandler(auto_gen_toggle, pattern="^auto_gen_inline$"))
    app.add_handler(CallbackQueryHandler(set_username_handler, pattern="^set_username_inline$"))
    app.add_handler(MessageHandler(filters.Regex("^(✅ Enable Friends Check|❌ Disable Friends Check|⬅️ Back)$"), toggle_friends_check))
    logger.info("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
