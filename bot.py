import logging
import asyncio
import requests
import os
import sys
import io
import re
from bs4 import BeautifulSoup
from telethon import TelegramClient, events

# Configuration
API_ID = 38659771
API_HASH = "6178147a40a23ade99f8b3a45f00e436"
BOT_TOKEN = "7966844330:AAE10tysbFmMnL3dIQhf1RHrNEwRUrpDJOU"
ALLOWED_GROUP_ID = -1003759432523
DOWNLOAD_DIR = "downloads"

# Logger setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Telegram Client
bot = TelegramClient('insta_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# Create download directory if not exists
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# Global Job Queue
# Item format: (chat_id, url_string, index_in_batch, total_in_batch)
JOB_QUEUE = asyncio.Queue()

def get_instagram_media_links(instagram_url):
    """
    Takes an Instagram post URL, queries media.mollygram.com,
    and returns a list of media download URLs found in the response.
    """
    base_url = "https://media.mollygram.com/"
    # Using the raw URL for data fetching as requested by the user
    params = {'url': instagram_url}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://mollygram.com/',
        'Origin': 'https://mollygram.com',
        'X-Requested-With': 'XMLHttpRequest'
    }

    try:
        logger.info(f"Fetching data for: {instagram_url}...")
        response = requests.get(base_url, params=params, headers=headers)
        response.raise_for_status()
        
        try:
            data = response.json()
        except Exception:
            logger.error(f"Error: content is not valid JSON. Content: {response.text[:500]}")
            return []

        if data.get("status") != "ok":
            logger.error(f"Error from API: {data.get('status')}")
            return []

        html_content = data.get("html", "")
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, 'html.parser')
        media_links = []
        
        download_buttons = soup.find_all('a', id='download-video')
        if not download_buttons:
             download_buttons = soup.find_all('a', class_='bg-gradient-success')

        for btn in download_buttons:
            href = btn.get('href')
            if href:
                media_links.append(href)

        return media_links

    except Exception as e:
        logger.error(f"Request failed: {e}")
        return []

async def worker():
    """
    Background worker that processes links one by one from the queue.
    """
    logger.info("Worker started...")
    while True:
        # Wait for a task
        task = await JOB_QUEUE.get()
        chat_id, original_url, idx, total = task
        
        # Determine Cleaned URL
        cleaned_url = original_url.split('?')[0].rstrip('/')
        
        # Send Status Message
        try:
            status_msg = await bot.send_message(chat_id, f"Processing {idx}/{total}\n{cleaned_url}", link_preview=False)
        except Exception as e:
            logger.error(f"Failed to send status message: {e}")
            JOB_QUEUE.task_done()
            continue

        try:
            # 1. Extract Links
            media_links = await asyncio.to_thread(get_instagram_media_links, original_url)
            
            if not media_links:
                await bot.send_message(chat_id, f"Error - No Media Found\n{cleaned_url}", link_preview=False)
                await status_msg.delete()
                JOB_QUEUE.task_done()
                await asyncio.sleep(1)
                continue

            # 2. Process Media
            total_media = len(media_links)
            
            for i, link in enumerate(media_links, 1):
                caption = f"{cleaned_url}"
                if total_media > 1:
                    caption = f"{i}/{total_media}\n{cleaned_url}"

                download_path = None
                try:
                    # Download content to disk (Server Save)
                    r = await asyncio.to_thread(requests.get, link)
                    if r.status_code == 200:
                        is_video = 'video' in r.headers.get('Content-Type', '')
                        ext = 'mp4' if is_video else 'jpg'
                        
                        # create unique filename
                        filename = f"{chat_id}_{idx}_{i}.{ext}"
                        download_path = os.path.join(DOWNLOAD_DIR, filename)
                        
                        with open(download_path, 'wb') as f:
                            f.write(r.content)
                        
                        # Upload from disk
                        await bot.send_file(chat_id, download_path, caption=caption, force_document=False)
                    else:
                        await bot.send_message(chat_id, f"Failed to download a file from: {cleaned_url}")
                except Exception as e:
                    logger.error(f"Error sending file: {e}")
                    await bot.send_message(chat_id, f"Failed to upload a file from: {cleaned_url}")
                finally:
                    # Clean up file from server
                    if download_path and os.path.exists(download_path):
                        os.remove(download_path)

            await status_msg.delete()

        except Exception as e:
            logger.error(f"Worker Error: {e}")
            await bot.send_message(chat_id, f"Error processing {cleaned_url}")
            # Try to delete status message if exists
            try:
                await status_msg.delete()
            except:
                pass
        
        # Mark task as done and wait a bit
        JOB_QUEUE.task_done()
        await asyncio.sleep(1)


@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.chat_id != ALLOWED_GROUP_ID and not event.is_private:
        return
    await event.respond("Hello! Send me Instagram post URLs (space separated) and I will queue them for processing.")

@bot.on(events.NewMessage(pattern='/update'))
async def update_handler(event):
    if event.chat_id != ALLOWED_GROUP_ID and not event.is_private:
        return

    msg = await event.reply("Checking for updates...")
    try:
        process = await asyncio.create_subprocess_shell(
            "git pull",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        output = stdout.decode().strip() or stderr.decode().strip()
        
        if "Already up to date" in output:
            await msg.edit(f"Bot is already up to date.\n`{output}`")
        else:
            await msg.edit(f"Update successful!\n`{output}`\nRestarting bot...")
            os.execl(sys.executable, sys.executable, *sys.argv)
            
    except Exception as e:
        await msg.edit(f"Update failed: {e}")
        logger.error(f"Update failed: {e}")

@bot.on(events.NewMessage)
async def message_handler(event):
    if event.chat_id != ALLOWED_GROUP_ID and not event.is_private:
        return

    # Ignore messages from the bot itself (prevents loops)
    if event.sender_id == (await bot.get_me()).id:
        return

    if event.text.startswith('/'):
        return

    ctx = event.text.strip()
    
    # 1. Regex to find all URLs
    url_pattern = r'(https?://(?:www\.)?instagram\.com/\S+)'
    urls = re.findall(url_pattern, ctx)
    
    if not urls:
        return

    # 2. Deduplicate
    seen = set()
    unique_urls = []
    for url in urls:
        if url not in seen:
            unique_urls.append(url)
            seen.add(url)
    urls = unique_urls

    # 3. Add to Queue
    count = 0
    total = len(urls)
    for idx, url in enumerate(urls, 1):
        await JOB_QUEUE.put((event.chat_id, url, idx, total))
        count += 1
    
    await event.reply(f"Added {count} links to the processing queue.")

def main():
    logger.info("Bot is running...")
    # Start the worker task loop
    bot.loop.create_task(worker())
    bot.run_until_disconnected()

if __name__ == '__main__':
    main()
