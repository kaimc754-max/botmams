#!/usr/bin/env python3
# merged_bot.py
# Combined: 2FA TOTP Telegram bot + TempMail Telegram bot
# Requirements: python-telegram-bot >=20, requests, pyotp
# pip install python-telegram-bot requests pyotp

import asyncio
import datetime
import logging
import random
import re
import string
import time
import uuid
from typing import Optional

import pyotp
import requests
from telegram import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --------------------------
# CONFIGURATION
# --------------------------
# NOTE: It's strongly recommended to move the token to an environment variable in production.
BOT_TOKEN = "8017229052:AAEb-YaMRVP7JkPiDFAufqmL1uSTEmEFxfc"

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --------------------------
# GLOBAL USER STORAGE (for TempMail features & UI state)
# --------------------------
user_data: dict = {}
# structure per chat_id:
# {
#   "emails": [ ... ],
#   "active": str | None,
#   "last_seen_id": None | str,
#   "username": None | str,
#   "auto_gen_on": bool,
#   "menu": "main" | "tempmail",
#   "awaiting_username": bool
# }

# --------------------------
# KNOWN SENDERS MAP
# --------------------------
KNOWN_SENDERS = {
    "google.com": "Google",
    "registration@facebookmail.com": "Facebook",
    "meta.com": "Meta (Facebook)",
    "twitter.com": "X (Twitter)",
    "discord.com": "Discord",
    "amazon.com": "Amazon",
    "microsoft.com": "Microsoft",
    "apple.com": "Apple",
    "noreply@telegram.org": "Telegram",
    "instagram.com": "Instagram",
    "tiktok.com": "TikTok",
    "netflix.com": "Netflix",
    "steamcommunity.com": "Steam",
    "reddit.com": "Reddit",
    "paypal.com": "PayPal",
    "snapchat.com": "Snapchat",
    "spotify.com": "Spotify",
    "linkedin.com": "LinkedIn",
    "uber.com": "Uber",
    "noreply@tm.openai.com": "Chat Gpt",
}

# --------------------------
# REPLY KEYBOARDS
# --------------------------
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🔐 2FA Authenticator"), KeyboardButton("📧 TempMail")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)

TEMPMail_SUBMENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📧 Generate Email"), KeyboardButton("📜 My Emails")],
        [KeyboardButton("⚙️ Set Username"), KeyboardButton("♻️ Auto-Gen Toggle")],
        [KeyboardButton("⬅️ Back")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)

# --------------------------
# 2FA / TOTP FUNCTIONS
# --------------------------
def calculate_totp(secret_key: str) -> tuple[Optional[str], int]:
    try:
        totp = pyotp.TOTP(secret_key)
        current_code = totp.now()
        current_time_seconds = int(time.time())
        time_remaining = 30 - (current_time_seconds % 30)
        return current_code, time_remaining
    except Exception as e:
        logger.error(f"Error calculating TOTP: {e}")
        return None, 0


def format_countdown_message(code: str, time_remaining: int) -> str:
    timer_emoji = "🟡"
    if time_remaining <= 5:
        timer_emoji = "🔴"
    elif time_remaining <= 15:
        timer_emoji = "🟠"
    message = (
        "Power By None\n\n"
        f"OTP CODE >> <code>'{code}'</code>\n"
        f"⏳ {timer_emoji} Expires in: {time_remaining:02d} seconds\n\n"
        "Power By None"
    )
    return message


async def countdown_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    job_data = job.data or {}
    chat_id = job_data.get("chat_id")
    message_id = job_data.get("message_id")
    secret_key = job_data.get("secret_key")
    if not chat_id or not message_id or not secret_key:
        job.schedule_removal()
        return
    code, time_remaining = calculate_totp(secret_key)
    if code is None:
        job.schedule_removal()
        return
    try:
        if time_remaining > 0:
            message = format_countdown_message(code, time_remaining)
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=message,
                parse_mode="HTML",
            )
        else:
            final_message = (
                "🔴 <b>CODE EXPIRED!</b> 🔴\n\n"
                "Your previous code has refreshed. Please send the secret key again "
                "or press the '2FA Authenticator' button to request a new code."
            )
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=final_message,
                parse_mode="HTML",
            )
            job.schedule_removal()
    except Exception:
        job.schedule_removal()


async def start_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE, secret_key: str) -> None:
    chat_id = update.effective_chat.id
    initial_code, initial_time_remaining = calculate_totp(secret_key)
    if initial_code is None or initial_time_remaining == 0:
        error_message = "⚠️ <b>Error:</b> Could not generate initial code. Please try again."
        await update.message.reply_text(error_message, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
        return
    initial_message_text = format_countdown_message(initial_code, initial_time_remaining)
    initial_message = await update.message.reply_text(
        initial_message_text, reply_markup=None, parse_mode="HTML"
    )
    job_data = {
        "chat_id": chat_id,
        "message_id": initial_message.message_id,
        "secret_key": secret_key,
    }
    context.job_queue.run_repeating(
        countdown_job,
        interval=1.0,
        first=1.0,
        data=job_data,
        name=f"otp_countdown_{chat_id}_{uuid.uuid4()}",
    )

# --------------------------
# TempMail Helpers
# --------------------------
def generate_random_name(min_len=6, max_len=12):
    chars = string.ascii_letters + string.digits
    length = random.randint(min_len, max_len)
    return "".join(random.choice(chars) for _ in range(length)).lower()


def generate_email(username_prefix=None):
    if username_prefix and username_prefix.isalnum():
        name = username_prefix
    else:
        name = generate_random_name()
    return f"{name}@mailto.plus"


def fetch_inbox(email):
    url = f"https://tempmail.plus/api/mails?email={email}&first_id=0&epin="
    try:
        res = requests.get(url, headers={"accept": "application/json"}, timeout=10)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def format_sender_name(sender_string):
    email_match = re.search(r"<([^@]+@[^>]+)>", sender_string)
    if email_match:
        email_address = email_match.group(1)
        domain = email_address.split("@")[-1]
        if domain in KNOWN_SENDERS:
            return KNOWN_SENDERS[domain]
    return sender_string.replace("<", " ").replace(">", "").strip()


def extract_otp(subject, content):
    otp_pattern = re.compile(
        r"(?:OTP|CODE|PIN|verification|one[\s_-]time).*?(\d{4,8})" r"|(\b\d{4,8}\b)",
        re.IGNORECASE | re.DOTALL,
    )
    match = otp_pattern.search(subject or "")
    if match:
        return match.group(1) or match.group(2)
    match = otp_pattern.search(content or "")
    if match:
        return match.group(1) or match.group(2)
    return None


# --------------------------
# USER DATA UTIL
# --------------------------
def initialize_user_data(chat_id: int):
    if chat_id not in user_data:
        user_data[chat_id] = {
            "emails": [],
            "active": None,
            "last_seen_id": None,
            "username": None,
            "auto_gen_on": False,
            "menu": "main",
            "awaiting_username": False,
        }


# --------------------------
# HANDLERS
# --------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    initialize_user_data(chat_id)
    user_data[chat_id]["menu"] = "main"
    user_data[chat_id]["awaiting_username"] = False
    welcome = "👋 মেনু থেকে একটি অপশন চয়ন করুন।"
    await update.message.reply_text(welcome, reply_markup=MAIN_KEYBOARD)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    initialize_user_data(chat_id)
    if text == "📧 TempMail":
        user_data[chat_id]["menu"] = "tempmail"
        await update.message.reply_text("📧 TempMail মেনু", reply_markup=TEMPMail_SUBMENU)
        return
    if text == "⬅️ Back":
        user_data[chat_id]["menu"] = "main"
        await update.message.reply_text("Back to main menu.", reply_markup=MAIN_KEYBOARD)
        return
    # ... other handlers trimmed for brevity ...


# --------------------------
# AUTO-FETCH BACKGROUND TASK
# --------------------------
async def auto_fetch(app: Application):
    while True:
        await asyncio.sleep(3)


# --------------------------
# STARTUP / MAIN
# --------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    try:
        app.create_task(auto_fetch(app))
    except Exception:
        asyncio.get_event_loop().create_task(auto_fetch(app))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
