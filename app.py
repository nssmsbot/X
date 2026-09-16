import os, re, sqlite3, hashlib, secrets, threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = "PASTE_YOUR_NEW_BOT_TOKEN_HERE"
DB_FILE = "users.db"

web = Flask(__name__)

@web.get("/")
def home():
    return "Telegram registration bot is running."

@web.get("/health")
def health():
    return {"ok": True}

def db():
    c = sqlite3.connect(DB_FILE)
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        telegram_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.commit()
    return c

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ":" + digest.hex()

def valid_email(email):
    return re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) is not None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("📝 Create Account", callback_data="register")],
          [InlineKeyboardButton("👤 My Account", callback_data="account")]]
    await update.message.reply_text(
        "স্বাগতম! নিজের account তৈরি করতে নিচের button চাপুন:",
        reply_markup=InlineKeyboardMarkup(kb))

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "register":
        context.user_data.clear()
        context.user_data["step"] = "name"
        await q.message.reply_text("👤 আপনার নাম লিখুন:")
    elif q.data == "account":
        c = db()
        row = c.execute("SELECT name,email,created_at FROM users WHERE telegram_id=?",
                        (q.from_user.id,)).fetchone()
        c.close()
        if row:
            await q.message.reply_text(
                f"👤 My Account\n\nName: {row[0]}\nEmail: {row[1]}\nCreated: {row[2]}")
        else:
            await q.message.reply_text("আপনার কোনো account নেই। /start দিয়ে শুরু করুন।")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("step")
    text = (update.message.text or "").strip()
    if not step:
        await update.message.reply_text("শুরু করতে /start লিখুন।")
        return

    if step == "name":
        if not 2 <= len(text) <= 80:
            await update.message.reply_text("2-80 অক্ষরের একটি নাম দিন।")
            return
        context.user_data["name"] = text
        context.user_data["step"] = "email"
        await update.message.reply_text("📧 আপনার email লিখুন:")

    elif step == "email":
        if not valid_email(text):
            await update.message.reply_text("সঠিক email address দিন।")
            return
        email = text.lower()
        c = db()
        exists = c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone()
        c.close()
        if exists:
            await update.message.reply_text("এই email আগে ব্যবহৃত হয়েছে। অন্য email দিন:")
            return
        context.user_data["email"] = email
        context.user_data["step"] = "password"
        await update.message.reply_text("🔐 Password দিন (কমপক্ষে 8 অক্ষর):")

    elif step == "password":
        if len(text) < 8:
            await update.message.reply_text("Password কমপক্ষে 8 অক্ষরের হতে হবে। আবার দিন:")
            return
        name, email = context.user_data["name"], context.user_data["email"]
        c = db()
        try:
            c.execute("INSERT INTO users(telegram_id,name,email,password_hash) VALUES(?,?,?,?)",
                      (update.effective_user.id, name, email, hash_password(text)))
            c.commit()
        except sqlite3.IntegrityError:
            await update.message.reply_text("Account তৈরি করা যায়নি; email হয়তো ব্যবহৃত হয়েছে।")
            c.close()
            return
        c.close()
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Account তৈরি হয়েছে!\n\n👤 Name: {name}\n📧 Email: {email}")

def run_web():
    port = int(os.environ.get("PORT", "8080"))
    web.run(host="0.0.0.0", port=port, use_reloader=False)

def main():
    if BOT_TOKEN == "PASTE_YOUR_NEW_BOT_TOKEN_HERE":
        raise RuntimeError("app.py-তে BOT_TOKEN-এর জায়গায় আপনার NEW Telegram Bot Token দিন.")
    threading.Thread(target=run_web, daemon=True).start()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(buttons))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.run_polling()

if __name__ == "__main__":
    main()
