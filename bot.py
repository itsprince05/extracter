import asyncio
import logging
import re
import os
import requests
import time
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor
from telethon import TelegramClient, events
import instaloader

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

# Initialize Instaloader
L = instaloader.Instaloader()
# Optional: Configure to not download compressed images, etc.
# L.download_pictures = False
# L.download_videos = False 
# We only use it to get metadata URLs.


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
            "📊 **Bulk Processing Status**\n\n"
            f"📨 **Total Tasks:** `{STATS['total']}`\n"
            f"✅ **Completed:** `{STATS['completed']}`\n"
            f"❌ **Failed:** `{STATS['failed']}`\n"
            f"⏳ **Remaining:** `{STATS['remaining']}`\n\n"
            "⚙️ _Processing..._"
        )
        
        if STATS['remaining'] == 0 and STATS['total'] > 0:
            text += "\n\n✨ **All tasks completed!**"

        await STATS['status_msg'].edit(text)
    except Exception as e:
        logger.error(f"Failed to update status message: {e}")

def fetch_media_task(url):
    """Synchronous function to fetch media links using Instaloader."""
    try:
        # Extract shortcode
        # Supports /p/, /reel/, /tv/
        shortcode_match = re.search(r'(?:/p/|/reel/|/tv/)([a-zA-Z0-9_-]+)', url)
        if not shortcode_match:
             return {'error': "Invalid URL format or could not extract shortcode"}
        
        shortcode = shortcode_match.group(1)
        
        # Fetch Post Metadata
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        
        media_items = []
        
        # Check if Sidecar (Album/Carousel)
        if post.typename == 'GraphSidecar':
            for node in post.get_sidecar_nodes():
                if node.is_video:
                    if node.video_url:
                        media_items.append({'url': node.video_url, 'is_video': True})
                else:
                    media_items.append({'url': node.display_url, 'is_video': False})
        
        # Check if Video
        elif post.is_video:
            if post.video_url:
                media_items.append({'url': post.video_url, 'is_video': True})
        
        # Image
        else:
            media_items.append({'url': post.url, 'is_video': False})
            
        if not media_items:
             return {'error': "No media found in post"}
             
        return {'media': media_items}

    except instaloader.LoginRequiredException as e:
        return {'error': f"Private Account/Login Required: {e}"}
    except instaloader.QueryReturnedNotFoundException as e:
        return {'error': f"Post Not Found: {e}"}
    except Exception as e:
        return {'error': f"Exception: {type(e).__name__} - {str(e)}"}

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
                
                STATS['completed'] += 1
            else:
                # Error
                error_reason = result.get('error', 'Unknown')
                raise Exception(error_reason)

        except Exception as e:
            STATS['failed'] += 1
            logger.error(f"Failed {url}: {e}")
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
        msg = await event.respond("🔄 **Update Requested**\n⬇️ Pulling latest code...")
        try:
            proc = await asyncio.create_subprocess_shell(
                "git pull",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                await msg.edit(f"✅ **Git Pull Success**\n`{stdout.decode().strip()}`\n\n♻️ Restarting System...")
                subprocess.Popen(["sudo", "systemctl", "restart", "extracter"])
                sys.exit(0)
            else:
                await msg.edit(f"❌ **Git Pull Failed**\n`{stderr.decode()}`")
        except Exception as e:
            await msg.edit(f"❌ **Error:** {e}")

@bot.on(events.NewMessage)
async def message_handler(event):
    if not event.is_private:
        return
    if event.message.text.startswith('/'):
        return
        
    text = event.message.text or ""
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
                    f"🔄 **Derived Queue** ({added} links)..."
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
                    f"🔄 **Queue Updated** (+{added})..."
                )
            
            await update_status_message()
            
            if not IS_PROCESSING:
                asyncio.create_task(process_queue())

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond("👋 Send Instagram links to extract.")

if __name__ == '__main__':
    bot.run_until_disconnected()