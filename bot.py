import asyncio
import logging
import re
import os
import requests
import time
import sys
import random
import subprocess
import json
from concurrent.futures import ThreadPoolExecutor
from telethon import TelegramClient, events


# --- Logging Configuration ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration ---
API_ID = 38659771
API_HASH = '6178147a40a23ade99f8b3a45f00e436'
BOT_TOKEN = "8533327762:AAHR1D4CyFpMQQ4NztXhET6OL4wL1kHNkQ4"



# Groups
GROUP_MEDIA = -1003759432523
GROUP_ERROR = -1003650307144


# Valid headers for requests mostly for download if needed, though instaloader handles its own metadata
# API_URL = "https://princeapps.com/insta.php" # Removed





# --- Client Initialization ---
bot = TelegramClient('controller_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# --- State Management ---
QUEUE = asyncio.Queue()
IS_PROCESSING = False

# Stats tracking
STATS = {
    'total': 0,
    'completed': 0,
    'failed': 0,
    'remaining': 0,
    'status_msg': None,
    'chat_id': None
}

# Executor for blocking IO
executor = ThreadPoolExecutor(max_workers=5)

def clean_instagram_url(url):
    """Removes query parameters to get the clean Instagram link."""
    return url.split('?')[0]

async def update_status_message():
    """Updates the status message in the chat."""
    if not STATS['status_msg']:
        return

    try:
        text = (
            "Bulk Processing Status\n\n"
            f"Total Tasks - {STATS['total']}\n"
            f"Completed - {STATS['completed']}\n"
            f"Failed - {STATS['failed']}\n"
            f"Remaining - {STATS['remaining']}\n\n"
        )
        
        if STATS['remaining'] == 0 and STATS['total'] > 0:
            text += "All tasks completed..."
        else:
            text += "Processing..."

        await STATS['status_msg'].edit(text)
    except Exception as e:
        logger.error(f"Failed to update status message: {e}")

    except Exception as e:
        logger.error(f"Failed to update status message: {e}")



def fetch_media_task(url):
    """Fetch media using reelsvideo.io Scraper."""
    try:
        clean_link = url.split('?')[0].rstrip('/')
        
        # Extract shortcode/id
        # supports /p/, /reel/, /tv/
        shortcode = None
        for pattern in [r'/p/([^/]+)', r'/reel/([^/]+)', r'/tv/([^/]+)']:
            match = re.search(pattern, clean_link)
            if match:
                shortcode = match.group(1)
                break
        
        if not shortcode:
             # Fallback if URL is weird, just try to use base
             target_endpoint = "https://reelsvideo.io/" # Fallback
        else:
             target_endpoint = f"https://reelsvideo.io/p/{shortcode}/"

        headers = {
            'authority': 'reelsvideo.io',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'accept-language': 'en-US,en;q=0.9',
            'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'origin': 'https://reelsvideo.io',
            'referer': 'https://reelsvideo.io/',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
        }
        
        data = {
            'id': url,
            'locale': 'en',
            'tt': 'e47e128f3c05058167cd0489686f359d', # Cloned from user HTML, likely standard or ignored
            'ts': int(time.time())
        }
        
        # 1. POST Request
        # Note: We try to hit the root first if specific endpoint fails, but user provided specific endpoint logic.
        # Let's try root "/" first as that is what the HTML form says (data-hx-post="/")
        r = requests.post("https://reelsvideo.io/", headers=headers, data=data, timeout=30)
        
        if r.status_code != 200:
             # Try the specific path as fallback if root fails or returns 404
             r = requests.post(target_endpoint, headers=headers, data=data, timeout=30)
             if r.status_code != 200:
                  return {'error': f"HTTP {r.status_code}"}
        
        html = r.text
        media_list = []
        msgs = []
        
        # 2. Extract Download Links using Regex
        # Looking for <a ... href="..." ... class="... download_link ... type_videos ..." ...>
        # The HTML sample has `class="... download_link type_videos ..."` and `type_images`
        
        # Regex to capture href inside an <a> tag that contains 'download_link'
        # Group 1: href
        # We assume the href is double-quoted
        
        # Find all <a> tags
        a_tags = re.findall(r'<a[^>]+>', html)
        
        for tag in a_tags:
            if 'download_link' in tag:
                # Extract href
                href_match = re.search(r'href="([^"]+)"', tag)
                if href_match:
                    media_url = href_match.group(1)
                    
                    # Determine type
                    is_video = 'type_videos' in tag
                    
                    # Add to list
                    # Avoid duplicates
                    if not any(m['url'] == media_url for m in media_list):
                        media_list.append({
                            'url': media_url,
                            'is_video': is_video
                        })
        
        # Check if sidecar detected (more than 1 item)
        if len(media_list) > 1:
            msgs.append(f"Multiple Sidecar\n{url}")
            
        if not media_list:
             return {'error': "No Media Found (reelsvideo.io)"}

        msgs = list(set(msgs))
        return {'media': media_list, 'msgs': msgs}

    except Exception as e:
        return {'error': f"Exception: {str(e)}"}

def download_media_task(media_url, is_video=False):
    """Synchronous function to download media to temp file."""
    try:
        # Determine extension based on type, fail-safe
        ext = 'mp4' if is_video else 'jpg'
            
        filename = f"temp_{int(time.time() * 1000000)}.{ext}"
        
        
        with requests.get(media_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return filename
    except Exception as e:
        logger.error(f"Download failed: {e}")
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except:
                pass
        return None

async def process_queue():
    """Main worker loop."""
    global IS_PROCESSING
    IS_PROCESSING = True
    loop = asyncio.get_event_loop()
    
    while not QUEUE.empty():
        url = await QUEUE.get()
        clean_url = clean_instagram_url(url)
        logger.info(f"Processing: {url}")
        
        try:
            # 1. Fetch Metadata (Run in Thread)
            result = await loop.run_in_executor(executor, fetch_media_task, url)
            
            if 'media' in result:
                media_items = result['media']
                # 2. Process Media
                for item in media_items:
                    media_link = item['url']
                    is_video = item['is_video']
                    
                    # Download (Run in Thread)
                    file_path = await loop.run_in_executor(executor, download_media_task, media_link, is_video)
                    
                    if file_path:
                        try:
                            # Upload (Telethon send_file is async)
                            await bot.send_file(
                                GROUP_MEDIA,
                                file_path,
                                caption=clean_url,
                                force_document=False,
                                supports_streaming=is_video
                            )
                        except Exception as e_up:
                            logger.error(f"Upload failed: {e_up}")
                        finally:
                            # Cleanup
                            if os.path.exists(file_path):
                                os.remove(file_path)
                            
                        await asyncio.sleep(1) # Rate limit
                
                # Send Side Channel Messages (No Audio, Multiple Sidecar)
                if 'msgs' in result:
                    for msg in result['msgs']:
                        try:
                            await bot.send_message(GROUP_ERROR, msg, link_preview=False)
                        except:
                            pass

                STATS['completed'] += 1
            else:
                # Error
                error_reason = result.get('error', 'Unknown')
                
                # Check for "Invalid" specific error
                if "Invalid" in error_reason:
                     await bot.send_message(GROUP_ERROR, f"Error - Invalid\n{url}", link_preview=False)
                else:
                     raise Exception(error_reason) # Trigger standard error handler

        except Exception as e:
            STATS['failed'] += 1
            # Standard error handler for exceptions
            try:
                await bot.send_message(
                    GROUP_ERROR, 
                    f"Error\n{url}", 
                    link_preview=False
                )
            except:
                pass
        
        # DEBUG: Send Dump if exists
        if os.path.exists('debug_dump.json'):
            try:
                await bot.send_file(
                    GROUP_ERROR,
                    'debug_dump.json',
                    caption=f"Debug Data for: {url}",
                    force_document=True
                )
            except Exception as e_dump:
                logger.error(f"Failed to send debug dump: {e_dump}")
            finally:
                os.remove('debug_dump.json')
        
        STATS['remaining'] = QUEUE.qsize()
        STATS['remaining'] = QUEUE.qsize()
        await update_status_message()
        await asyncio.sleep(5) # 5 Second Delay (User Request)
            
    IS_PROCESSING = False
    await update_status_message()

@bot.on(events.NewMessage(pattern='/update'))
async def update_handler(event):
    if event.chat_id == GROUP_MEDIA or event.is_private:
        msg = await event.respond("Update Requested\nPulling latest code...")
        try:
            proc = await asyncio.create_subprocess_shell(
                "git pull",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                await msg.edit(f"Git Pull Success\n{stdout.decode().strip()}\n\nRestarting System...")
                subprocess.Popen(["sudo", "systemctl", "restart", "extracter"])
                sys.exit(0)
            else:
                await msg.edit(f"Git Pull Failed\n{stderr.decode()}")
        except Exception as e:
            await msg.edit(f"Error: {e}")



@bot.on(events.NewMessage)
async def message_handler(event):
    if not event.is_private:
        return
    
    chat_id = event.chat_id
    text = event.message.text or ""

    if text.startswith('/'):
        return

    # --- Normal Link Processing ---
    urls = re.findall(r'(https?://(?:www\.)?instagram\.com/\S+)', text)
    
    if urls:
        added = 0
        for url in urls:
            await QUEUE.put(url)
            added += 1
            
        if added > 0:
            STATS['chat_id'] = event.chat_id
            
            if not IS_PROCESSING and STATS['remaining'] == 0:
                STATS['total'] = added
                STATS['completed'] = 0
                STATS['failed'] = 0
                STATS['remaining'] = QUEUE.qsize()
                STATS['status_msg'] = await event.respond(
                    f"Derived Queue ({added} links)..."
                )
            else:
                STATS['total'] += added
                STATS['remaining'] = QUEUE.qsize()
                if STATS['status_msg']:
                    try:
                        await STATS['status_msg'].delete()
                    except:
                        pass
                STATS['status_msg'] = await event.respond(
                    f"Queue Updated (+{added})..."
                )
            
            await update_status_message()
            
            if not IS_PROCESSING:
                asyncio.create_task(process_queue())

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond("👋 Send Instagram links to extract.")

if __name__ == '__main__':
    bot.run_until_disconnected()