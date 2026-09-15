import os
import threading

from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

BOT_TOKEN = os.environ["8603037987:AAGGsVvwtd5POCxed6JFPRdwoQ43gUFjuJk"]

# Official Facebook signup page.
FACEBOOK_SIGNUP_URL = "https://www.facebook.com/r.php"

web = Flask(__name__)

@web.get("/")
def home():
    return "Facebook signup redirect bot is running."

@web.get("/health")
def health():
    return {"ok": True}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📘 Create Facebook Account", url=FACEBOOK_SIGNUP_URL)]
    ]
    await update.message.reply_text(
        "Facebook account তৈরি করতে নিচের button-এ চাপুন।\n"
        "Facebook-এর official signup page খুলবে। "
        "আপনার password/OTP এই bot-এ পাঠাবেন না।",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

def run_web():
    port = int(os.environ.get("PORT", "8080"))
    web.run(host="0.0.0.0", port=port, use_reloader=False)

def main():
    threading.Thread(target=run_web, daemon=True).start()

    bot = Application.builder().token(BOT_TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CallbackQueryHandler(button))

    print("Bot started.")
    bot.run_polling()

if __name__ == "__main__":
    main()
