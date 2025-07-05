import os
import json
import time
import logging
import shutil
import requests
import traceback
import difflib
from bs4 import BeautifulSoup
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from threading import Thread
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler
import asyncio
import nest_asyncio

# Apply asyncio patch for environments like Jupyter or Render
nest_asyncio.apply()

# Load .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
bot = Bot(BOT_TOKEN)

# Constants
DATA_FILE = "data.json"
DATA_BACKUP = "data_backup.json"
LOG_FILE = "bot.log"
CHECK_INTERVAL = 60  # seconds

# Setup logging
log_handler = RotatingFileHandler(LOG_FILE, maxBytes=50000, backupCount=2)
logging.basicConfig(level=logging.INFO, handlers=[log_handler])
logger = logging.getLogger(__name__)

# Global vars
user_urls = {}
paused_users = set()
last_seen_posts = {}
last_content = {}  # NEW

# ------------------------ Admin Notifier ------------------------ #
async def notify_admin(message: str):
    try:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[ERROR] Failed to send admin alert: {e}")

# ------------------------ Persistence ------------------------ #
def load_data():
    global user_urls, paused_users, last_content
    try:
        if os.path.exists(DATA_FILE) and os.path.getsize(DATA_FILE) > 0:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                user_urls.update(data.get("user_urls", {}))
                paused_users.update(data.get("paused_users", []))
                last_content.update(data.get("last_content", {}))  # NEW
        else:
            user_urls.clear()
            paused_users.clear()
            last_content.clear()  # NEW
    except Exception as e:
        logger.warning(f"Failed to load {DATA_FILE}: {e}")
        user_urls.clear()
        paused_users.clear()
        last_content.clear()

def save_data():
    try:
        data = {
            "user_urls": user_urls,
            "paused_users": list(paused_users),
            "last_content": last_content  # NEW
        }
        if os.path.exists(DATA_FILE):
            shutil.copy(DATA_FILE, DATA_BACKUP)
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving {DATA_FILE}: {e}")

# ------------------------ Commands ------------------------ #
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) != 1:
        await update.message.reply_text("❌ Usage: /add <website_url>")
        return

    url = context.args[0]
    user_urls.setdefault(chat_id, [])
    if url in user_urls[chat_id]:
        await update.message.reply_text("ℹ️ Already monitoring:\n" + url)
        return

    user_urls[chat_id].append(url)
    save_data()
    await update.message.reply_text(f"✅ Now monitoring:\n{url}")

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    urls = user_urls.get(chat_id, [])
    if not urls:
        await update.message.reply_text("ℹ️ No URLs are being monitored.")
        return
    msg = "🔍 Monitored URLs:\n" + "\n".join([f"{i+1}. {url}" for i, url in enumerate(urls)])
    await update.message.reply_text(msg)

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) != 1:
        await update.message.reply_text("❌ Usage: /remove <website_url>")
        return

    url = context.args[0]
    urls = user_urls.get(chat_id, [])
    if url in urls:
        urls.remove(url)
        user_urls[chat_id] = urls
        save_data()
        await update.message.reply_text(f"🗑️ Removed:\n{url}")
    else:
        await update.message.reply_text("❌ URL not found in your list.")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id in user_urls:
        user_urls[chat_id] = []
        save_data()
        await update.message.reply_text("🗑️ All URLs cleared.")
    else:
        await update.message.reply_text("ℹ️ You have no URLs to clear.")

async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    paused_users.add(chat_id)
    save_data()
    await update.message.reply_text("⏸️ Monitoring paused. Use /resume to start again.")

async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id in paused_users:
        paused_users.remove(chat_id)
        save_data()
        await update.message.reply_text("▶️ Monitoring resumed.")
    else:
        await update.message.reply_text("ℹ️ Monitoring is already active.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *Available Commands:*\n\n"
        "/add <url> – Monitor a website\n"
        "/list – Show monitored websites\n"
        "/remove <url> – Remove a specific URL\n"
        "/clear – Clear all monitored URLs\n"
        "/pause – Stop receiving updates\n"
        "/resume – Resume monitoring\n"
        "/help – Show this help message"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# ------------------------ Scraper Thread ------------------------ #
def categorize(text: str) -> str:
    text = text.lower()
    if "result" in text:
        return "📄 Result"
    elif "admit card" in text or "hall ticket" in text:
        return "🎫 Admit Card"
    elif "recruitment" in text or "vacancy" in text or "job" in text:
        return "💼 Job Update"
    else:
        return "📌 General Update"

def highlight_diff(old, new):
    diff = list(difflib.ndiff(old.splitlines(), new.splitlines()))
    highlighted = []
    for line in diff:
        if line.startswith("+ "):
            highlighted.append(f"➕ {line[2:]}")
        elif line.startswith("- "):
            highlighted.append(f"➖ {line[2:]}")
    return "\n".join(highlighted[:30])

def check_websites():
    while True:
        for chat_id, urls in user_urls.items():
            if chat_id in paused_users:
                continue
            for url in urls:
                try:
                    response = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                    soup = BeautifulSoup(response.text, 'html.parser')
                    post_section = soup.find("div", class_="post")
                    if not post_section:
                        continue

                    content = post_section.text.strip()
                    content_hash = hash(content)

                    if url not in last_seen_posts or last_seen_posts[url] != content_hash:
                        category = categorize(content)

                        old_content = last_content.get(url, "")
                        diff_text = highlight_diff(old_content, content) if old_content else content[:500]

                        last_seen_posts[url] = content_hash
                        last_content[url] = content
                        save_data()

                        msg = (
                            f"{category}\n"
                            f"🔗 <a href='{url}'>{url}</a>\n\n"
                            f"<pre>{diff_text[:3000]}</pre>"
                        )
                        bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML", disable_web_page_preview=True)

                except Exception as e:
                    logger.error(f"Error checking {url}: {e}")
        time.sleep(CHECK_INTERVAL)

# ------------------------ Main ------------------------ #
async def main():
    try:
        load_data()
        app = ApplicationBuilder().token(BOT_TOKEN).build()

        app.add_handler(CommandHandler("add", add))
        app.add_handler(CommandHandler("list", list_urls))
        app.add_handler(CommandHandler("remove", remove))
        app.add_handler(CommandHandler("clear", clear))
        app.add_handler(CommandHandler("pause", pause))
        app.add_handler(CommandHandler("resume", resume))
        app.add_handler(CommandHandler("help", help_command))

        Thread(target=check_websites, daemon=True).start()

        await notify_admin("✅ *Bot started and is now monitoring websites.*")
        await app.run_polling()
    except Exception as e:
        error_msg = f"❌ *Bot crashed:*\n```\n{traceback.format_exc()}\n```"
        await notify_admin(error_msg)

# ------------------------ Start App ------------------------ #
if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
