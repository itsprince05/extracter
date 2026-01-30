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

import instaloader

def get_instagram_media_links(instagram_url, unique_id):
    """
    Uses Instaloader to extract media links (Images & Videos).
    Returns (media_links_list, debug_file_path).
    """
    media_links = []
    debug_file_path = None
    
    # Initialize Instaloader
    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False, 
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False
    )

    try:
        logger.info(f"Extracting with Instaloader: {instagram_url}")
        
        # Extract Shortcode
        shortcode = None
        if "/p/" in instagram_url:
            shortcode = instagram_url.split("/p/")[1].split("/")[0].split("?")[0]
        elif "/reel/" in instagram_url:
            shortcode = instagram_url.split("/reel/")[1].split("/")[0].split("?")[0]
        
        if not shortcode:
            logger.error("Could not parse shortcode")
            return [], None

        # Fetch Post
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        
        # 1. Carousel (Sidecar)
        if post.typename == 'GraphSidecar':
            for node in post.get_sidecar_nodes():
                if node.is_video:
                    media_links.append(node.video_url)
                else:
                    media_links.append(node.display_url)
        
        # 2. Single Video
        elif post.is_video:
            media_links.append(post.video_url)
            
        # 3. Single Image
        else:
            media_links.append(post.url)

        return media_links, None

    except Exception as e:
        logger.error(f"Instaloader failed: {e}")
        # Save error to debug file
        timestamp = int(time.time())
        debug_filename = f"error_instaloader_{unique_id}_{timestamp}.txt"
        debug_file_path = os.path.join(DOWNLOAD_DIR, debug_filename)
        with open(debug_file_path, "w") as f:
            f.write(str(e))
            
        return [], debug_file_path

async def worker():
    """
    Background worker that processes links one by one from the queue.
    """
    logger.info("Worker started...")
    while True:
        # Wait for a task
        task = await JOB_QUEUE.get()
        chat_id, original_url, idx, total = task
        
        # Determine Cleaned URL (for caption only)
        cleaned_url = original_url.split('?')[0].rstrip('/')
        unique_req_id = f"{chat_id}_{idx}"
        
        # Send Status Message
        try:
            status_msg = await bot.send_message(chat_id, f"Processing {idx}/{total}\n{cleaned_url}", link_preview=False)
        except Exception as e:
            logger.error(f"Failed to send status message: {e}")
            JOB_QUEUE.task_done()
            continue

        try:
            # 1. Extract Links
            media_links, debug_file = await asyncio.to_thread(get_instagram_media_links, original_url, unique_req_id)
            
            if not media_links:
                # ERROR: Send RAW URL
                await bot.send_message(ERROR_GROUP_ID, f"Error - No Media Found\n{original_url}", link_preview=False)
                
                # Upload Debug File
                if debug_file and os.path.exists(debug_file):
                    try:
                        await bot.send_file(ERROR_GROUP_ID, debug_file, caption="Debug Response From API")
                    except Exception as e:
                        logger.error(f"Failed to send debug file: {e}")
                    finally:
                        os.remove(debug_file)

                await status_msg.delete()
                JOB_QUEUE.task_done()
                
                # Check for batch completion even on error
                if idx == total:
                    await bot.send_message(chat_id, f"{total} links processed.")
                    
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
                    # 1. Download Helper
                    async def process_download_wrapper(url, base_filename):
                        # Wrapper to allow asyncio calling and debug messaging
                        
                        async def send_debug(msg):
                            try:
                                await bot.send_message(ERROR_GROUP_ID, f"Debug: {msg}")
                            except:
                                pass

                        # Helper for actual request
                        def perform_request(target_url):
                            headers = {
                                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                                'Referer': 'https://media.mollygram.com/'
                            }
                            return requests.get(target_url, stream=True, headers=headers, timeout=60, allow_redirects=True)

                        # Strategy 1: Direct URL
                        try:
                            r = await asyncio.to_thread(perform_request, url)
                            
                            # Check if we got a valid media response
                            content_type = r.headers.get('Content-Type', '')
                            if r.status_code == 200 and ('video' in content_type or 'image' in content_type):
                                # Proceed with file save
                                is_video = 'video' in content_type
                                ext = 'mp4' if is_video else 'jpg'
                                final_filename = f"{base_filename}.{ext}"
                                final_path = os.path.join(DOWNLOAD_DIR, final_filename)
                                
                                await asyncio.to_thread(save_file, r, final_path)
                                return final_path, is_video
                            else:
                                await send_debug(f"Strategy 1 failed. Status: {r.status_code}, Type: {content_type}\nURL: {url[:50]}...")
                                
                        except Exception as e:
                             await send_debug(f"Strategy 1 Error: {e}")

                        # Strategy 2: Extract 'media' param if present (Direct CDN Fallback)
                        if 'media=' in url:
                            try:
                                from urllib.parse import urlparse, parse_qs, unquote
                                parsed = urlparse(url)
                                params = parse_qs(parsed.query)
                                media_url = params.get('media', [None])[0]
                                
                                if media_url:
                                    decoded_media_url = unquote(media_url)
                                    # await send_debug(f"Trying Strategy 2: {decoded_media_url[:50]}...")
                                    
                                    r = await asyncio.to_thread(perform_request, decoded_media_url)
                                    content_type = r.headers.get('Content-Type', '')
                                    
                                    if r.status_code == 200 and ('video' in content_type or 'image' in content_type):
                                        is_video = 'video' in content_type
                                        ext = 'mp4' if is_video else 'jpg'
                                        final_filename = f"{base_filename}.{ext}"
                                        final_path = os.path.join(DOWNLOAD_DIR, final_filename)
                                        
                                        await asyncio.to_thread(save_file, r, final_path)
                                        return final_path, is_video
                                    else:
                                         await send_debug(f"Strategy 2 failed. Type: {content_type}")
                            except Exception as e:
                                await send_debug(f"Strategy 2 Error: {e}")

                        return None, None

                    def save_file(response, path):
                        with open(path, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=8192): 
                                f.write(chunk)

                    base_name = f"{chat_id}_{idx}_{i}"
                    download_path, is_video = await process_download_wrapper(link, base_name)
                    
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
        # Mark task as done and wait a bit
        JOB_QUEUE.task_done()
        
        # Check for batch completion for successful case
        if idx == total:
             await bot.send_message(chat_id, f"{total} links processed.")
             
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
