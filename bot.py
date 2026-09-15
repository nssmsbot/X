import os
import sqlite3
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# Railway Variables:
# BOT_TOKEN = your BotFather token
# ADMIN_IDS = your Telegram user ID, e.g. 123456789
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()

try:
    ADMIN_IDS = {
        int(x.strip())
        for x in ADMIN_IDS_RAW.split(",")
        if x.strip()
    }
except ValueError:
    ADMIN_IDS = set()

DB_FILE = Path("bot_data.db")


def init_db():
    with sqlite3.connect(DB_FILE) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_type TEXT NOT NULL,
                service TEXT NOT NULL,
                number TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'QUEUED',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)


def add_job(test_type, service, number=""):
    with sqlite3.connect(DB_FILE) as con:
        cur = con.execute(
            "INSERT INTO jobs(test_type, service, number) VALUES (?, ?, ?)",
            (test_type, service, number),
        )
        return cur.lastrowid


def get_jobs(limit=20):
    with sqlite3.connect(DB_FILE) as con:
        return con.execute(
            """SELECT id, test_type, service, number, status, created_at
               FROM jobs ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()


def get_stats():
    with sqlite3.connect(DB_FILE) as con:
        return con.execute(
            "SELECT status, COUNT(*) FROM jobs GROUP BY status"
        ).fetchall()


def is_admin(update: Update):
    user = update.effective_user
    return bool(user and user.id in ADMIN_IDS)


def menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Website Test", callback_data="website"),
            InlineKeyboardButton("📱 App Test", callback_data="app"),
        ],
        [
            InlineKeyboardButton("➕ Create Test Job", callback_data="create"),
            InlineKeyboardButton("📋 Jobs", callback_data="jobs"),
        ],
        [
            InlineKeyboardButton("📊 Statistics", callback_data="stats"),
            InlineKeyboardButton("🔄 Refresh", callback_data="home"),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("Access denied.")
        return

    await update.message.reply_text(
        "🛠 Admin Control Panel\n\nSelect an option:",
        reply_markup=menu(),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(update):
        await query.edit_message_text("Access denied.")
        return

    if query.data == "home":
        text = "🛠 Admin Control Panel\n\nSelect an option:"

    elif query.data == "website":
        text = (
            "🌐 Website Test\n\n"
            "Use this for websites that you own or are authorized to test.\n\n"
            "To create a queued test job, use 'Create Test Job'."
        )

    elif query.data == "app":
        text = (
            "📱 App Test\n\n"
            "Use this for apps that you own or are authorized to test.\n\n"
            "To create a queued test job, use 'Create Test Job'."
        )

    elif query.data == "create":
        add_job("website", "authorized-test")
        text = (
            "✅ Test job created.\n\n"
            "Type: Website\n"
            "Service: authorized-test\n"
            "Status: QUEUED\n\n"
            "You can modify the service/job logic inside bot.py."
        )

    elif query.data == "jobs":
        rows = get_jobs()
        if not rows:
            text = "📋 Jobs\n\nNo jobs yet."
        else:
            lines = ["📋 Latest Jobs\n"]
            for row in rows:
                job_id, test_type, service, number, status, created = row
                lines.append(
                    f"#{job_id} • {test_type} • {service} • {status}"
                )
            text = "\n".join(lines)

    elif query.data == "stats":
        rows = get_stats()
        if not rows:
            text = "📊 Statistics\n\nNo jobs yet."
        else:
            text = "📊 Statistics\n\n" + "\n".join(
                f"• {status}: {count}" for status, count in rows
            )

    else:
        text = "Unknown option."

    await query.edit_message_text(text[:4000], reply_markup=menu())


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing. Add BOT_TOKEN in Railway Variables."
        )

    if not ADMIN_IDS:
        raise RuntimeError(
            "ADMIN_IDS is missing or invalid. "
            "Add your numeric Telegram User ID in Railway Variables."
        )

    init_db()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("Telegram bot started.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
