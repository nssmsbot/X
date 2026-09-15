import threading

from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ============================================================
# PUT YOUR NEW TELEGRAM BOT TOKEN BETWEEN THE QUOTES BELOW.
# Do NOT use the old token that was visible in your screenshot.
# ============================================================
BOT_TOKEN = "8603037987:AAHQoeiYOoSQ_m-vVcflWnC8gFOgYhau0Xc"

FACEBOOK_SIGNUP_URL = "https://www.facebook.com/r.php"

web = Flask(__name__)


@web.get("/")
def home():
    return "Facebook signup bot is running."


@web.get("/health")
def health():
    return {"ok": True}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton(
                "📘 Create Facebook Account",
                url=FACEBOOK_SIGNUP_URL
            )
        ]
    ]

    await update.message.reply_text(
        "Facebook account তৈরি করতে নিচের button-এ চাপুন।\n"
        "এটি Facebook-এর official signup page খুলবে।",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


def run_web():
    # Railway automatically provides PORT.
    port = int(__import__("os").environ.get("PORT", "8080"))
    web.run(host="0.0.0.0", port=port, use_reloader=False)


def main():
    if BOT_TOKEN == "PASTE_YOUR_NEW_BOT_TOKEN_HERE":
        raise RuntimeError(
            "Open app.py and replace PASTE_YOUR_NEW_BOT_TOKEN_HERE "
            "with your NEW Telegram Bot Token."
        )

    threading.Thread(target=run_web, daemon=True).start()

    bot = Application.builder().token(BOT_TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CallbackQueryHandler(button))

    print("Bot started.")
    bot.run_polling()


if __name__ == "__main__":
    main()
