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
    """Fetch media using PrinceApps API."""
    try:
        api_url = "https://princeapps.com/insta.php"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
        }
        
        # 1. Call API
        # verify=False because sometimes these custom APIs exhibit SSL issues, though not strictly required if valid
        r = requests.get(api_url, params={'url': url}, headers=headers, timeout=30)
        
        if r.status_code != 200:
            return {'error': f"API Error: HTTP {r.status_code}"}
            
        try:
            data = r.json()
        except:
            return {'error': "Invalid JSON Response"}
            
        if not data:
            return {'error': "No Media Found (Empty Response)"}
            
        media_list = []
        msgs = []
        
        # 2. Process URLs
        # The API returns a simple list of string URLs
        if isinstance(data, list):
            for media_url in data:
                # We don't need HEAD request anymore, downloader handles it
                media_list.append({
                    'url': media_url,
                    'is_video': False # Placeholder, ignored by downloader
                })
        
        if len(media_list) > 1:
            msgs.append(f"Multiple Sidecar\n{url}")
            
        if not media_list:
             return {'error': "No Media Found"}

        msgs = list(set(msgs))
        return {'media': media_list, 'msgs': msgs}

    except Exception as e:
        return {'error': f"Exception: {str(e)}"}

def download_media_task(media_url):
    """Synchronous function to download media and auto-detect type."""
    try:
        temp_base = f"temp_{int(time.time() * 1000000)}"
        temp_file = temp_base # no extension
        
        with requests.get(media_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(temp_file, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        # Auto-detect type from file signature (Magic Bytes)
        is_video = False
        ext = 'bin'
        
        with open(temp_file, 'rb') as f:
            header = f.read(12)
            
        header_hex = header.hex().upper()
        
        if header_hex.startswith('FFD8FF'):
            ext = 'jpg'
            is_video = False
        elif header_hex.startswith('89504E47'):
            ext = 'png'
            is_video = False
        elif '66747970' in header_hex or '6D6F6F76' in header_hex: # ftyp or moov (mp4 atoms)
             # Common MP4 sigs start with 000000... ftyp...
             # '66747970' is 'ftyp' in hex
             ext = 'mp4'
             is_video = True
        else:
             # Fallback: assume MP4 if unsure because images are usually strictly headers
             # But let's check content-type from headers if possible? 
             # No, we already downloaded. Let's just try MP4 as fallback for safety or JPG?
             # User says "videos aren't sending", so likely they are MP4s being treated as Images.
             # If we default to MP4 for unknown, Telegram handles 'video' upload as file if invalid.
             ext = 'mp4' 
             is_video = True

        final_filename = f"{temp_base}.{ext}"
        os.rename(temp_file, final_filename)
        
        return final_filename, is_video

    except Exception as e:
        logger.error(f"Download failed: {e}")
        if os.path.exists(temp_base):
             try: os.remove(temp_base)
             except: pass
        return None, False
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
                    # is_video hint from fetcher is unreliable, ignore it, logic is in downloader now
                    
                    # Download (Run in Thread)
                    # Returns tuple: (file_path, is_video_bool)
                    file_path, is_video = await loop.run_in_executor(executor, download_media_task, media_link)
                    
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
        if os.path.exists('debug_dump.html'):
            try:
                await bot.send_file(
                    GROUP_ERROR,
                    'debug_dump.html',
                    caption=f"Debug HTML for: {url}",
                    force_document=True
                )
            except Exception as e_dump:
                logger.error(f"Failed to send debug dump: {e_dump}")
            finally:
                os.remove('debug_dump.html')
        
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