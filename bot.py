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

# API
API_URL = "https://princeapps.com/insta.php"

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
    """Synchronous function to fetch media links."""
    try:
        # 1. Fetch from API
        params = {'url': url}
        resp = requests.get(API_URL, params=params, timeout=30)
        
        if resp.status_code != 200:
            return {'error': f"HTTP {resp.status_code}"}
            
        try:
            data = resp.json()
        except:
             return {'error': "Invalid JSON"}
             
        if isinstance(data, list) and data:
            return {'media': data}
        elif isinstance(data, dict):
            return {'error': data.get('error', 'Unknown Error')}
        else:
            return {'error': "Empty response"}
            
    except Exception as e:
        return {'error': str(e)}

def download_media_task(media_url):
    """Synchronous function to download media to temp file."""
    try:
        ext = 'jpg'
        if '.mp4' in media_url:
            ext = 'mp4'
        elif '.png' in media_url:
            ext = 'png'
            
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
                media_list = result['media']
                # 2. Process Media
                for media_link in media_list:
                    # Download (Run in Thread)
                    file_path = await loop.run_in_executor(executor, download_media_task, media_link)
                    
                    if file_path:
                        try:
                            # Upload (Telethon send_file is async)
                            # Identify video for supports_streaming
                            is_video = file_path.endswith('.mp4')
                            
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
                    f"Error\n{url}", 
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