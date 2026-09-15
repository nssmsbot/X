import os
import json
import secrets
import sqlite3
import time
import hashlib
from urllib.request import Request, urlopen

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ====== PUT YOUR SETTINGS HERE ======
BOT_TOKEN = "8603037987:AAH0r1zl-t85wcVGvNEtwRlUCAu53cWF2lY"
ADMIN_IDS = {8385712100}

# Your own/authorized SMS gateway:
SMS_GATEWAY_URL = "https://YOUR-SMS-GATEWAY.example/send"
SMS_GATEWAY_API_KEY = "PASTE_YOUR_SMS_GATEWAY_KEY_HERE"

OTP_EXPIRY_SECONDS = 300
MAX_SENDS_PER_MINUTE = 10
MAX_SENDS_PER_DAY = 1000
# ====================================

DB_FILE = "otp_bot.db"

def init_db():
    with sqlite3.connect(DB_FILE) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS otp(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT, otp_hash TEXT, status TEXT,
            created INTEGER, expires INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS sends(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT, created INTEGER, status TEXT)""")

def allowed_to_send():
    t = int(time.time())
    with sqlite3.connect(DB_FILE) as c:
        minute = c.execute(
            "SELECT COUNT(*) FROM sends WHERE created>=? AND status='SENT'",
            (t-60,)).fetchone()[0]
        day = c.execute(
            "SELECT COUNT(*) FROM sends WHERE created>=? AND status='SENT'",
            (t-86400,)).fetchone()[0]
    if minute >= MAX_SENDS_PER_MINUTE:
        return False, f"Per-minute limit reached: {MAX_SENDS_PER_MINUTE}"
    if day >= MAX_SENDS_PER_DAY:
        return False, f"Daily limit reached: {MAX_SENDS_PER_DAY}"
    return True, ""

def send_sms(phone, message):
    if "YOUR-SMS-GATEWAY" in SMS_GATEWAY_URL or SMS_GATEWAY_API_KEY.startswith("PASTE_"):
        return False, "Configure your authorized SMS gateway in bot.py."
    data = json.dumps({"to": phone, "message": message}).encode()
    req = Request(SMS_GATEWAY_URL, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SMS_GATEWAY_API_KEY}"
    })
    try:
        with urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300, f"HTTP {r.status}"
    except Exception as e:
        return False, str(e)[:200]

def admin(update):
    return bool(update.effective_user and update.effective_user.id in ADMIN_IDS)

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Send OTP", callback_data="send"),
         InlineKeyboardButton("📊 Statistics", callback_data="stats")],
        [InlineKeyboardButton("📋 History", callback_data="history"),
         InlineKeyboardButton("⚙️ Limits", callback_data="limits")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin(update):
        await update.message.reply_text("Access denied.")
        return
    await update.message.reply_text("🔐 OTP Admin Panel", reply_markup=menu())

async def sendotp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /sendotp 8801XXXXXXXXX")
        return

    phone = context.args[0].strip()
    ok, reason = allowed_to_send()
    if not ok:
        await update.message.reply_text("⛔ " + reason)
        return

    otp = "".join(secrets.choice("0123456789") for _ in range(6))
    t = int(time.time())
    sent, detail = send_sms(
        phone,
        f"Your verification code is {otp}. It expires in 5 minutes."
    )
    status = "SENT" if sent else "FAILED"

    with sqlite3.connect(DB_FILE) as c:
        c.execute(
            "INSERT INTO otp(phone,otp_hash,status,created,expires) VALUES(?,?,?,?,?)",
            (phone, hashlib.sha256(otp.encode()).hexdigest(),
             status, t, t + OTP_EXPIRY_SECONDS))
        c.execute(
            "INSERT INTO sends(phone,created,status) VALUES(?,?,?)",
            (phone, t, status))

    await update.message.reply_text(
        f"✅ OTP sent to {phone}" if sent
        else f"❌ Send failed: {detail}"
    )

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not admin(update):
        return

    with sqlite3.connect(DB_FILE) as c:
        if q.data == "stats":
            sent = c.execute("SELECT COUNT(*) FROM sends WHERE status='SENT'").fetchone()[0]
            failed = c.execute("SELECT COUNT(*) FROM sends WHERE status='FAILED'").fetchone()[0]
            text = f"📊 Statistics\n\n✅ Sent: {sent}\n❌ Failed: {failed}"
        elif q.data == "history":
            rows = c.execute(
                "SELECT phone,status,created FROM sends ORDER BY id DESC LIMIT 20"
            ).fetchall()
            text = "📋 History\n\n" + (
                "\n".join(f"• {r[0]} — {r[1]}" for r in rows)
                if rows else "No records."
            )
        elif q.data == "limits":
            text = f"⚙️ Limits\n\nPer minute: {MAX_SENDS_PER_MINUTE}\nPer day: {MAX_SENDS_PER_DAY}"
        elif q.data == "send":
            text = "📱 Send OTP\n\nUse /sendotp 8801XXXXXXXXX"
        else:
            text = "🔐 OTP Admin Panel"

    await q.edit_message_text(text, reply_markup=menu())

def main():
    if BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("Put your BotFather token in BOT_TOKEN.")
    if not ADMIN_IDS or 123456789 in ADMIN_IDS:
        raise RuntimeError("Replace the example ADMIN_IDS value.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sendotp", sendotp))
    app.add_handler(CallbackQueryHandler(buttons))
    print("OTP admin bot started.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
