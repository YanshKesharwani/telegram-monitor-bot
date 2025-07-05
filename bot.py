import logging
import time
import requests
from bs4 import BeautifulSoup
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext
from threading import Thread

# Config
BOT_TOKEN = "8070341201:AAGbgdghe0w0Cm4hr5kYahI5--K6fFACyG4"
CHECK_INTERVAL = 60  # in seconds
user_urls = {}  # {chat_id: [url1, url2]}
last_seen_posts = {}  # {url: last_content_hash}

logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)

# Add command handler
def add(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    if len(context.args) != 1:
        update.message.reply_text("❌ Usage: /add <website_url>")
        return
    url = context.args[0]
    user_urls.setdefault(chat_id, []).append(url)
    update.message.reply_text(f"✅ Added for monitoring:\n{url}")

# Check website for changes
def check_websites():
    while True:
        for chat_id, urls in user_urls.items():
            for url in urls:
                try:
                    response = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                    soup = BeautifulSoup(response.text, 'html.parser')

                    # For sarkariresult.com.im - extract top post
                    post_section = soup.find("div", class_="post")
                    if not post_section:
                        continue
                    latest_text = post_section.text.strip()
                    content_hash = hash(latest_text)

                    if url not in last_seen_posts or last_seen_posts[url] != content_hash:
                        last_seen_posts[url] = content_hash
                        msg = f"🆕 New update detected!\n\n🔗 <b>Website:</b> <a href='{url}'>{url}</a>\n\n📰 {latest_text[:400]}..."
                        bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML", disable_web_page_preview=True)

                except Exception as e:
                    print(f"Error checking {url}: {e}")

        time.sleep(CHECK_INTERVAL)

# Start the bot
def main():
    updater = Updater(BOT_TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("add", add))

    # Start website checker thread
    Thread(target=check_websites, daemon=True).start()

    # Start Telegram bot
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
