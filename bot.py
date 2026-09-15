from pathlib import Path
import re


# ===== BOT SETTINGS =====
BOT_TOKEN = "8603037987:AAGGsVvwtd5POCxed6JFPRdwoQ43gUFjuJk"
ADMIN_IDS = {8385712100}
# =========================

src = Path("/mnt/data/bot_limited.py")
text = src.read_text(encoding="utf-8")

# Service selection state.
text = text.replace(
    'DB_FILE = "otp_bot.db"',
    '''DB_FILE = "otp_bot.db"
SERVICES = ["Facebook", "WhatsApp", "Instagram", "Telegram", "Other Service"]
SELECTED_SERVICE = {}'''
)

# Add service column and migration.
old_db = '''c.execute("""CREATE TABLE IF NOT EXISTS sends(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT, created INTEGER, status TEXT)""")'''
new_db = '''c.execute("""CREATE TABLE IF NOT EXISTS sends(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT, created INTEGER, status TEXT, service TEXT)""")
        cols = [r[1] for r in c.execute("PRAGMA table_info(sends)").fetchall()]
        if "service" not in cols:
            c.execute("ALTER TABLE sends ADD COLUMN service TEXT DEFAULT 'Other Service'")'''
text = text.replace(old_db, new_db)

# Bottom-style menu: buttons are all at the bottom of the message.
start = text.index("def menu():")
end = text.index("\n\nasync def start", start)
menus = '''def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Select Service", callback_data="services")],
        [InlineKeyboardButton("📤 Send OTP", callback_data="send")],
        [InlineKeyboardButton("📊 Statistics", callback_data="stats"),
         InlineKeyboardButton("📋 History", callback_data="history")],
        [InlineKeyboardButton("⚙️ Limits", callback_data="limits")],
    ])

def service_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📘 Facebook", callback_data="service:0")],
        [InlineKeyboardButton("🟢 WhatsApp", callback_data="service:1")],
        [InlineKeyboardButton("📸 Instagram", callback_data="service:2")],
        [InlineKeyboardButton("✈️ Telegram", callback_data="service:3")],
        [InlineKeyboardButton("➕ Other Service", callback_data="service:4")],
        [InlineKeyboardButton("🔙 Back", callback_data="back")],
    ])
'''
text = text[:start] + menus + text[end:]

# Require a selected service for sending.
text = text.replace(
    'phone = context.args[0].strip()\n    ok, reason = allowed_to_send(phone)',
    '''phone = context.args[0].strip()
    service = SELECTED_SERVICE.get(update.effective_user.id)
    if not service:
        await update.message.reply_text(
            "📱 Select a service first:",
            reply_markup=service_menu()
        )
        return
    ok, reason = allowed_to_send(phone)'''
)

# Store service with each send.
text = text.replace(
    'INSERT INTO sends(phone,created,status) VALUES(?,?,?)',
    'INSERT INTO sends(phone,created,status,service) VALUES(?,?,?,?)'
)
text = text.replace(
    '(phone, t, status))',
    '(phone, t, status, service))'
)

# Replace callback handler body with service-aware version.
start = text.index("async def buttons(")
end = text.index("\n\ndef main():", start)
handler = '''async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not admin(update):
        return

    if q.data == "services":
        await q.edit_message_text("📱 Select Service", reply_markup=service_menu())
        return

    if q.data == "back":
        await q.edit_message_text("🔐 OTP Admin Panel", reply_markup=menu())
        return

    if q.data.startswith("service:"):
        try:
            idx = int(q.data.split(":", 1)[1])
            service = SERVICES[idx]
        except (ValueError, IndexError):
            await q.edit_message_text("Invalid service.", reply_markup=menu())
            return
        SELECTED_SERVICE[update.effective_user.id] = service
        await q.edit_message_text(
            f"✅ Selected: {service}\\n\\nUse /sendotp 8801XXXXXXXXX",
            reply_markup=menu()
        )
        return

    with sqlite3.connect(DB_FILE) as c:
        if q.data == "stats":
            sent = c.execute(
                "SELECT COUNT(*) FROM sends WHERE status='SENT'"
            ).fetchone()[0]
            failed = c.execute(
                "SELECT COUNT(*) FROM sends WHERE status='FAILED'"
            ).fetchone()[0]
            text = f"📊 Statistics\\n\\n✅ Sent: {sent}\\n❌ Failed: {failed}"

        elif q.data == "history":
            rows = c.execute(
                "SELECT phone,status,created,service "
                "FROM sends ORDER BY id DESC LIMIT 20"
            ).fetchall()
            text = "📋 History\\n\\n" + (
                "\\n".join(
                    f"• {r[3] or 'Other Service'} — {r[0]} — {r[1]}"
                    for r in rows
                ) if rows else "No records."
            )

        elif q.data == "limits":
            text = (
                f"⚙️ Limits\\n\\n"
                f"Per minute: {MAX_SENDS_PER_MINUTE}\\n"
                f"Per day: {MAX_SENDS_PER_DAY}\\n"
                f"Same number cooldown: {PER_NUMBER_COOLDOWN_SECONDS // 60} minutes"
            )

        elif q.data == "send":
            service = SELECTED_SERVICE.get(update.effective_user.id)
            if service:
                text = f"📤 Send OTP\\n\\nService: {service}\\n\\nUse /sendotp 8801XXXXXXXXX"
            else:
                text = "📤 Send OTP\\n\\nSelect a service first."

        else:
            text = "🔐 OTP Admin Panel"

    await q.edit_message_text(text, reply_markup=menu())
'''
text = text[:start] + handler + text[end:]

# Update start screen.
text = text.replace(
    'await update.message.reply_text("🔐 OTP Admin Panel", reply_markup=menu())',
    'await update.message.reply_text("🔐 OTP Admin Panel\\n\\nSelect a service below.", reply_markup=menu())'
)

out = Path("/mnt/data/bot_service_select.py")
out.write_text(text, encoding="utf-8")
print(out)
