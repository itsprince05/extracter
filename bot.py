import logging
import time
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
MEDIA_GROUP_ID = -1003759432523
ERROR_GROUP_ID = -1003822781655
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
    # Use cleaned URL for the API to avoid parameter issues and improve success rate
    clean_url = instagram_url.split('?')[0].rstrip('/')
    
    base_url = "https://media.mollygram.com/"
    # URGENT FIX: Use the RAW URL (instagram_url) for the API request as requested by the user.
    # Do NOT use clean_url for the params.
    params = {'url': instagram_url}
    
    # Simple headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    # Retry logic
    for attempt in range(3):
        try:
            logger.info(f"Fetching data for: {instagram_url} (Attempt {attempt+1})")
            response = requests.get(base_url, params=params, headers=headers, timeout=10)
            
            if response.status_code != 200:
                logger.error(f"API returned status {response.status_code}")
                # If 429/403, maybe wait a bit
                if response.status_code in [429, 503]:
                    time.sleep(2)
                    continue
                continue # Try again or fail
            
            try:
                data = response.json()
            except Exception:
                logger.error(f"Error: content is not valid JSON. Content: {response.text[:200]}")
                continue

            if data.get("status") != "ok":
                logger.error(f"Error from API: {data.get('status')}")
                continue

            html_content = data.get("html", "")
            if not html_content:
                continue

            soup = BeautifulSoup(html_content, 'html.parser')
            media_links = []
            
            download_buttons = soup.find_all('a', id='download-video')
            if not download_buttons:
                download_buttons = soup.find_all('a', class_='bg-gradient-success')

            for btn in download_buttons:
                href = btn.get('href')
                if href:
                    media_links.append(href)

            if media_links:
                return media_links
        
        except Exception as e:
            logger.error(f"Request failed (Attempt {attempt+1}): {e}")
            time.sleep(1)
    
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
                await bot.send_message(ERROR_GROUP_ID, f"Error - No Media Found\n{cleaned_url}", link_preview=False)
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
                    # Download content to disk (Streamed)
                    def download_media(url, path=None):
                        with requests.get(url, stream=True) as r:
                            r.raise_for_status()
                            
                            # Determine filename if not provided (temp logic to get ext)
                            is_video = 'video' in r.headers.get('Content-Type', '')
                            ext = 'mp4' if is_video else 'jpg'
                            
                            if path is None:
                                # We need to return path and ext if not provided, but here we construct path outside
                                return None, ext, is_video
                                
                            with open(path, 'wb') as f:
                                for chunk in r.iter_content(chunk_size=8192): 
                                    f.write(chunk)
                            return True, ext, is_video

                    # 1. Peek headers to determine extension and path
                    # We do a quick head request or just stream start? 
                    # Simpler: Just start the get in thread, use logic to define path, then write.
                    # Since we need 'idx' and 'i' for filename, we do it all in one custom wrapper or split.
                    
                    # Let's do a cleaner custom function for the thread
                    def process_download(url, base_filename):
                         with requests.get(url, stream=True) as r:
                            if r.status_code != 200:
                                return None, None
                            
                            is_video = 'video' in r.headers.get('Content-Type', '')
                            ext = 'mp4' if is_video else 'jpg'
                            final_filename = f"{base_filename}.{ext}"
                            final_path = os.path.join(DOWNLOAD_DIR, final_filename)
                            
                            with open(final_path, 'wb') as f:
                                for chunk in r.iter_content(chunk_size=8192): 
                                    f.write(chunk)
                            return final_path, is_video

                    base_name = f"{chat_id}_{idx}_{i}"
                    download_path, is_video = await asyncio.to_thread(process_download, link, base_name)
                    
                    if download_path and os.path.exists(download_path):
                        # Upload from disk
                        # supports_streaming=True allows videos to play while downloading
                        await bot.send_file(
                            MEDIA_GROUP_ID, 
                            download_path, 
                            caption=caption, 
                            force_document=False,
                            supports_streaming=True if is_video else False
                        )
                    else:
                        await bot.send_message(ERROR_GROUP_ID, f"Failed to download a file from: {cleaned_url}")

                except Exception as e:
                    logger.error(f"Error sending file: {e}")
                    await bot.send_message(ERROR_GROUP_ID, f"Failed to upload a file from: {cleaned_url}")
                finally:
                    # Clean up file from server
                    if download_path and os.path.exists(download_path):
                        os.remove(download_path)

            await status_msg.delete()

        except Exception as e:
            logger.error(f"Worker Error: {e}")
            await bot.send_message(ERROR_GROUP_ID, f"Error processing {cleaned_url}")
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
            
            # Prepare args for restart, ensuring 'updated' flag is present
            new_args = [arg for arg in sys.argv if arg != 'updated']
            new_args.append('updated')
            
            os.execl(sys.executable, sys.executable, *new_args)
            
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
    
    # Check if restarted after update
    if 'updated' in sys.argv:
        bot.loop.create_task(bot.send_message(MEDIA_GROUP_ID, "Bot is updated and running"))

    # Start the worker task loop
    bot.loop.create_task(worker())
    bot.run_until_disconnected()

if __name__ == '__main__':
    main()
