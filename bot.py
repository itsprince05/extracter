import asyncio
import logging
import re
import os
import requests
import time
import sys
import random
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


# Valid headers for requests mostly for download if needed, though instaloader handles its own metadata
# API_URL = "https://princeapps.com/insta.php" # Removed


# Initialize Instaloader
# Moved inside function for fresh session per request
# L = instaloader.Instaloader()

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

def fetch_media_task(url):
    """Fetch media using direct GraphQL query."""
    try:
        # Extract shortcode
        shortcode_match = re.search(r'(?:/p/|/reel/|/tv/)([a-zA-Z0-9_-]+)', url)
        if not shortcode_match:
             return {'error': "Invalid URL format"}
        
        shortcode = shortcode_match.group(1)
        
        # GraphQL Endpoint construction
        # Using the doc_id seen in user logs: 8845758582119845
        graphql_url = "https://www.instagram.com/graphql/query"
        params = {
            'variables': f'{{"shortcode":"{shortcode}"}}',
            'doc_id': '8845758582119845',
            'server_timestamps': 'true'
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'X-IG-App-ID': '936619743392459' # Standard public app id
        }

        resp = requests.get(graphql_url, params=params, headers=headers, timeout=30)
        
        if resp.status_code != 200:
             return {'error': f"HTTP {resp.status_code}"}
             
        try:
            data = resp.json()
        except:
             return {'error': "Invalid JSON Response"}
             
        if 'data' not in data or not data['data'].get('xdt_shortcode_media'):
            return {'error': "Invalid"} # Becomes 'Error - Invalid' in logic
            
        media_node = data['data']['xdt_shortcode_media']
        media_items = []
        side_channel_msgs = []
        
        type_name = media_node.get('__typename')
        
        # 1. Image
        if type_name == 'XDTGraphImage':
            media_items.append({'url': media_node['display_url'], 'is_video': False})
            
        # 2. Video
        elif type_name == 'XDTGraphVideo':
            vid_url = media_node.get('video_url')
            if vid_url:
                media_items.append({'url': vid_url, 'is_video': True})
                
            if media_node.get('has_audio') is False:
                side_channel_msgs.append(f"Error - No Audio\n{url}")
                
        # 3. Sidecar
        elif type_name == 'XDTGraphSidecar':
            side_channel_msgs.append(f"Multiple Sidecar\n{url}") # Notification req
            
            edges = media_node.get('edge_sidecar_to_children', {}).get('edges', [])
            for edge in edges:
                node = edge.get('node', {})
                node_type = node.get('__typename')
                
                if node_type == 'XDTGraphImage':
                     if node.get('display_url'):
                        media_items.append({'url': node['display_url'], 'is_video': False})
                        
                elif node_type == 'XDTGraphVideo':
                    if node.get('video_url'):
                        media_items.append({'url': node['video_url'], 'is_video': True})
                        
                    if node.get('has_audio') is False:
                        # Prevent duplicate no-audio msgs for same link? 
                        # User req implies simply sending it.
                        # Using set to avoid spamming 10 msgs for 10 slides?
                        # Let's append, unique filter later if needed.
                        side_channel_msgs.append(f"Error - No Audio\n{url}")
        
        if not media_items:
             return {'error': "No media found"}
             
        # Remove duplicates from side_channel_msgs to avoid spamming
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