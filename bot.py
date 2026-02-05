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
import yt_dlp

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
            f"Total Tasks: {STATS['total']}\n"
            f"Completed: {STATS['completed']}\n"
            f"Failed: {STATS['failed']}\n"
            f"Remaining: {STATS['remaining']}\n\n"
            "Processing..."
        )
        
        if STATS['remaining'] == 0 and STATS['total'] > 0:
            text += "\n\nAll tasks completed!"

        await STATS['status_msg'].edit(text)
    except Exception as e:
        logger.error(f"Failed to update status message: {e}")

    except Exception as e:
        logger.error(f"Failed to update status message: {e}")



def fetch_media_task(url):
    """Fetch media using Authenticated yt-dlp."""
    try:
        # Configure yt-dlp with cookies
        ydl_opts = {
            'quiet': True, 
            'no_warnings': True,
            'extract_flat': False,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'cookiefile': 'cookies.txt', # Persist session
            'noplaylist': False, # Allow parsing sidecars
            'ignore_no_formats_error': True, # Vital for Image posts
        }

        media_items = []
        side_channel_msgs = []
        
        # Helper to process yt-dlp entry dict
        def process_entry(entry):
            # Check for specific sidecar entry type or determine by codecs
            is_video = False
            
            # 1. Determine Type
            # If vcodec is present and not none, it's likely video.
            # However, allow explicit check for known image extensions.
            if entry.get('vcodec') != 'none' and entry.get('vcodec') is not None:
                is_video = True
            
            if entry.get('ext') in ['jpg', 'png', 'webp', 'heic']:
                is_video = False
            
            final_url = None
            
            # 2. Extract URL based on Type
            if is_video:
                # Video: Prioritize formats with Audio (acodec != none) to avoid silent GIFs
                formats = entry.get('formats', [])
                if formats:
                    # Filter for formats with both video and audio
                    # Sort by resolution/quality if possible, usually last is best
                    best_audio_video = [
                        f for f in formats 
                        if f.get('vcodec') != 'none' 
                        and f.get('acodec') != 'none'
                        and f.get('protocol') in ['https', 'http'] # Avoid m3u8 if possible for direct download
                    ]
                    
                    if best_audio_video:
                        final_url = best_audio_video[-1].get('url') # Best quality with audio
                        
                    # Fallback: Just best format found (yt-dlp default)
                    if not final_url:
                        final_url = formats[-1].get('url')

                # Fallback to direct URL if no formats parsed
                if not final_url:
                    final_url = entry.get('url')

                # Check Audio for Side Channel Message
                if entry.get('acodec') == 'none':
                     # Double check if we picked a url with audio? 
                     pass # We handled selection above.

            else:
                # Image
                final_url = entry.get('url')
                if not final_url:
                    # Often in thumbnails for IG
                    thumbnails = entry.get('thumbnails', [])
                    if thumbnails:
                        final_url = thumbnails[-1].get('url')
            
            if final_url:
                media_items.append({'url': final_url, 'is_video': is_video})

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except yt_dlp.utils.DownloadError as e:
                err_str = str(e).lower()
                if 'login required' in err_str or 'sign in' in err_str or '401' in err_str:
                     if os.path.exists('cookies.txt'):
                         return {'error': "Cookies Expired. Please upload new cookies.txt"}
                     return {'error': "Login Required (Use /login)"}
                if 'checkpoint' in err_str or 'challenge' in err_str:
                     return {'error': "Checkpoint Required (Open App & Approve)"}
                if '404' in err_str or 'unavailable' in err_str:
                     return {'error': "Invalid"}
                return {'error': str(e)}
            except Exception as e:
                return {'error': f"Extraction Error: {e}"}

        # Check for Sidecar (Playlist)
        if 'entries' in info:
             side_channel_msgs.append(f"Multiple Sidecar\n{url}")
             for entry in info['entries']:
                 process_entry(entry)
        else:
             process_entry(info)
        
        if not media_items:
             return {'error': "Invalid"} 
             
        # Dedup messages
        side_channel_msgs = list(set(side_channel_msgs))
             
        return {'media': media_items, 'msgs': side_channel_msgs}

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
                    f"Error: {e}\n{url}", 
                    link_preview=False
                )
            except:
                pass
        
        STATS['remaining'] = QUEUE.qsize()
        await update_status_message()
        await asyncio.sleep(1)
            
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
async def file_handler(event):
    """Handle cookies.txt upload."""
    if not event.is_private or not event.file:
        return

    # Check if it looks like a text file
    filename = event.file.name or ""
    if filename.lower().endswith('.txt') or 'cookies' in filename.lower():
        path = await event.download_media(file='cookies.txt')
        await event.respond(f"✅ **Cookies File Uploaded!**\nSaved as: {path}\n\nThe bot will now use these cookies for extraction. Try sending a link now.")
        # Stop propagation so message_handler doesn't trigger
        raise events.StopPropagation

@bot.on(events.NewMessage)
async def message_handler(event):
    if not event.is_private:
        return
    
    chat_id = event.chat_id
    text = event.message.text or ""

    # --- JSON Cookie Support ---
    if text.strip().startswith(('{"url":', '[{"domain"')):
        try:
            data = json.loads(text)
            cookies = data.get('cookies') if isinstance(data, dict) else data
            
            if not isinstance(cookies, list):
                raise ValueError("No cookie list found")

            # Convert to Netscape Format
            with open('cookies.txt', 'w') as f:
                f.write("# Netscape HTTP Cookie File\n")
                for c in cookies:
                    domain = c.get('domain', '')
                    flag = 'TRUE' if domain.startswith('.') else 'FALSE'
                    path = c.get('path', '/')
                    secure = 'TRUE' if c.get('secure') else 'FALSE'
                    expiration = str(int(c.get('expirationDate', 0)))
                    name = c.get('name', '')
                    value = c.get('value', '')
                    
                    f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expiration}\t{name}\t{value}\n")
            
            await event.respond("✅ **Cookies Text Imported!**\nAccess restored. You can now send links.")
            return
        except Exception as e:
            await event.respond(f"❌ **Invalid Cookie JSON:** {e}")
            return

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
    await event.respond("👋 Send Instagram links to extract.\n📂 Upload cookies.txt to fix login errors.")

if __name__ == '__main__':
    bot.run_until_disconnected()