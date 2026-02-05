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

# Login Conversation States
LOGIN_STATES = {} # chat_id -> state_name
LOGIN_DATA = {}   # chat_id -> {username, password, ...}

# Initialize Instaloader Globally to Persist Session
L = instaloader.Instaloader()
# Try load session if exists
try:
    files = [f for f in os.listdir('.') if f.startswith('session-')]
    if files:
        user = files[0].replace('session-', '')
        L.load_session_to_context(user)
        logger.info(f"Loaded session for {user}")
except Exception as e:
    logger.warning(f"No session loaded: {e}")

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

# --- Login Logic ---
def attempt_login_task(username, password):
    """Run blocking login in executor."""
    try:
        L.login(username, password)
        L.save_session_to_file()
        return {'status': 'success'}
    except instaloader.TwoFactorAuthRequiredException:
        return {'status': '2fa_required'}
    except instaloader.BadCredentialsException:
        return {'status': 'error', 'msg': 'Invalid Password'}
    except instaloader.ConnectionException as e:
        err_str = str(e)
        if 'checkpoint_required' in err_str or 'challenge' in err_str:
            match = re.search(r'(https://www\.instagram\.com/challenge/\S+)', err_str)
            if match:
                return {'status': 'checkpoint', 'url': match.group(1)}
            return {'status': 'error', 'msg': 'Checkpoint Required: Verify in Instagram App'}
        return {'status': 'error', 'msg': str(e)}
    except Exception as e:
        return {'status': 'error', 'msg': str(e)}

def attempt_2fa_task(code):
    try:
        L.two_factor_login(code)
        L.save_session_to_file()
        return {'status': 'success'}
    except Exception as e:
        return {'status': 'error', 'msg': str(e)}

def fetch_media_task(url):
    """Fetch media using Authenticated Instaloader."""
    try:
        shortcode_match = re.search(r'(?:/p/|/reel/|/tv/)([a-zA-Z0-9_-]+)', url)
        if not shortcode_match:
             return {'error': "Invalid URL format"}
        
        shortcode = shortcode_match.group(1)
        
        try:
            # Use the global authenticated 'L' instance
            post = instaloader.Post.from_shortcode(L.context, shortcode)
        except instaloader.ConnectionException as e:
             if '401' in str(e) or 'redirect' in str(e).lower():
                 return {'error': f"HTTP 401 - Rate Limited (Check Login)"}
             return {'error': str(e)}
        except instaloader.LoginRequiredException:
             return {'error': "Login Required - Credentials Invalid or Account Private"}
        except Exception as e:
             return {'error': f"Metadata Fetch Failed: {e}"}
        
        # Access Raw Node Data (The user's JSON structure)
        node = post._node
        
        media_items = []
        side_channel_msgs = []
        
        # Helper to process a node dict
        def process_node(n, is_sidecar_child=False):
            t_name = n.get('__typename')
            
            # 1. Image
            if t_name == 'XDTGraphImage' or t_name == 'GraphImage':
                if n.get('display_url'):
                    media_items.append({'url': n['display_url'], 'is_video': False})
            
            # 2. Video
            elif t_name == 'XDTGraphVideo' or t_name == 'GraphVideo':
                v_url = n.get('video_url')
                if v_url:
                    media_items.append({'url': v_url, 'is_video': True})
                
                # Audio Check (Only if explicitly False)
                if n.get('has_audio') is False:
                     side_channel_msgs.append(f"Error - No Audio\n{url}")
            
            # 3. Sidecar
            elif t_name == 'XDTGraphSidecar' or t_name == 'GraphSidecar':
                if not is_sidecar_child:
                    side_channel_msgs.append(f"Multiple Sidecar\n{url}")
                
                edges = n.get('edge_sidecar_to_children', {}).get('edges', [])
                for edge in edges:
                    child_node = edge.get('node', {})
                    process_node(child_node, is_sidecar_child=True)

        # Start Processing
        try:
            process_node(node)
        except Exception as e:
            return {'error': f"Parsing Error: {e}"}
        
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

@bot.on(events.NewMessage(pattern='/login'))
async def login_handler(event):
    if not event.is_private:
        return
    LOGIN_STATES[event.chat_id] = 'WAITING_USERNAME'
    LOGIN_DATA[event.chat_id] = {}
    await event.respond("Instagram Login\n\nPlease enter your Username:")

@bot.on(events.NewMessage)
async def message_handler(event):
    if not event.is_private:
        return
    
    chat_id = event.chat_id
    text = event.message.text or ""

    if text.startswith('/'):
        return

    # --- Login Flow ---
    if chat_id in LOGIN_STATES:
        state = LOGIN_STATES[chat_id]
        
        if state == 'WAITING_USERNAME':
            LOGIN_DATA[chat_id]['username'] = text.strip()
            LOGIN_STATES[chat_id] = 'WAITING_PASSWORD'
            await event.respond("Enter your Password:")
            return

        elif state == 'WAITING_PASSWORD':
            LOGIN_DATA[chat_id]['password'] = text.strip()
            msg = await event.respond("Attempting Login...")
            
            # Run blocking login in thread
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(
                executor, 
                attempt_login_task, 
                LOGIN_DATA[chat_id]['username'],
                LOGIN_DATA[chat_id]['password']
            )
            
            if res['status'] == 'success':
                del LOGIN_STATES[chat_id]
                del LOGIN_DATA[chat_id]
                await msg.edit("Login Successful! Session saved.")
            
            elif res['status'] == '2fa_required':
                LOGIN_STATES[chat_id] = 'WAITING_OTP'
                await msg.edit("2FA Required\nPlease enter the SMS/App Code:")
            
            elif res['status'] == 'checkpoint':
                del LOGIN_STATES[chat_id]
                del LOGIN_DATA[chat_id]
                # Clean URL for display
                url = res.get('url', 'App').rstrip(' .')
                await msg.edit(f"Checkpoint Required\nPlease open this link:\n{url}\n\nClick 'This was me', then try /login again.")

            else:
                del LOGIN_STATES[chat_id]
                del LOGIN_DATA[chat_id]
                await msg.edit(f"Login Failed: {res.get('msg')}")
            return

        elif state == 'WAITING_OTP':
            msg = await event.respond("Verifying 2FA Code...")
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(
                executor, 
                attempt_2fa_task, 
                text.strip()
            )
            
            if res['status'] == 'success':
                del LOGIN_STATES[chat_id]
                del LOGIN_DATA[chat_id]
                await msg.edit("2FA Login Successful! Session saved.")
            else:
                del LOGIN_STATES[chat_id] # Reset on fail to avoid stuck loop
                del LOGIN_DATA[chat_id]
                await msg.edit(f"2FA Failed: {res.get('msg')}")
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
    await event.respond("👋 Send Instagram links.\nUse `/login` to authenticate.")

if __name__ == '__main__':
    bot.run_until_disconnected()