import asyncio
import logging
import os
import sys
import re
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError

# --- Configuration ---
# API Credentials
API_ID = 38659771
API_HASH = '6178147a40a23ade99f8b3a45f00e436'
CONTROLLER_BOT_TOKEN = "8533327762:AAHR1D4CyFpMQQ4NztXhET6OL4wL1kHNkQ4"

# --- Advanced Configuration ---
TARGET_PRIMARY = "@PinterestSave_ROBot"
TARGET_FALLBACK = "@YouTube_instagram_saver_bot"
GROUP_MEDIA = -1003759432523
GROUP_ERROR = -1003650307144

# Error signatures (Russian text provided by user)
ERROR_SIGNATURES = [
    "Не удалось скачать этот видеоролик",
    "Соцсеть вернула ошибку",
    "failed to download", 
    "error"
]

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Clients
bot = TelegramClient('controller_bot', API_ID, API_HASH).start(bot_token=CONTROLLER_BOT_TOKEN)
user = TelegramClient("user_session", API_ID, API_HASH)

# State
LOGIN_STATE = {'phone': None, 'otp_requested': False, 'hash': None}
LINK_QUEUE = asyncio.Queue()
IS_PROCESSING = False

# --- Controller Bot (Interacts with YOU) ---

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond(
        "**Multi-Link Reflector Bot** 🤖\n\n"
        "1. **/login** - Login your User Account (required).\n"
        "2. **Send Links** - Send Instagram links here (bulk supported).\n\n"
        f"**Routing:**\nPrimary: `{TARGET_PRIMARY}`\nFallback: `{TARGET_FALLBACK}`\n"
        f"Media Group: `{GROUP_MEDIA}`\nError Group: `{GROUP_ERROR}`"
    )

@bot.on(events.NewMessage(pattern='/login'))
async def login_step1(event):
    if not event.is_private:
        await event.respond("⚠️ Security: Please send `/login` in my **Private Message (DM)** only.", link_preview=False)
        return

    chat_id = event.chat_id
    await event.respond("📱 **Login Process Initiated**\n\nPlease send your phone number in international format.\nExample: `+919876543210`")
    LOGIN_STATE['chat_id'] = chat_id
    LOGIN_STATE['step'] = 'ask_phone'

@bot.on(events.NewMessage(pattern='/export'))
async def export_session(event):
    if not event.is_private: return
    try:
        await user.connect()
        if not await user.is_user_authorized():
            await event.respond("❌ You are not logged in yet.")
            return
        sess_string = user.session.save()
        await event.respond(f"🔑 **Your Session String** (Keep Safe!):\n\n`{sess_string}`")
    except Exception as e:
        await event.respond(f"❌ Error exporting: {e}")

@bot.on(events.NewMessage(pattern='/update'))
async def update_handler(event):
    # Allow in Media Group (anyone) or Private DM
    if event.chat_id == GROUP_MEDIA or event.is_private:
        msg = await event.respond("🔄 **Update Requested**\n⬇️ Pulling latest code...")
        try:
            # 1. Git Pull
            proc = await asyncio.create_subprocess_shell(
                "git pull",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                await msg.edit(f"✅ **Git Pull Success**\n`{stdout.decode().strip()}`\n\n♻️ Restarting System...")
                # 2. Restart Service
                import subprocess
                subprocess.Popen(["sudo", "systemctl", "restart", "extracter"])
                sys.exit(0)
            else:
                await msg.edit(f"❌ **Git Pull Failed**\n`{stderr.decode()}`")
        except Exception as e:
            await msg.edit(f"❌ **Error:** {e}")

@bot.on(events.NewMessage())
async def message_handler(event):
    # Ignore commands
    if event.message.text.startswith('/'): return
    
    chat_id = event.chat_id
    text = event.message.text.strip()
    
    # --- Login Flow ---
    if LOGIN_STATE.get('step') == 'ask_phone' and LOGIN_STATE.get('chat_id') == chat_id:
        LOGIN_STATE['phone'] = text
        status_msg = await event.respond("🔄 Initializing User Client...")
        
        try:
            if not user.is_connected():
                await user.connect()
            
            if await user.is_user_authorized():
                await status_msg.edit("✅ Already logged in!")
                LOGIN_STATE['step'] = None
                return

            await status_msg.edit(f"🔄 Sending OTP to {text}...")
            sent_code = await user.send_code_request(text)
            LOGIN_STATE['hash'] = sent_code.phone_code_hash
            LOGIN_STATE['step'] = 'ask_otp'
            await status_msg.edit(f"📩 **OTP Sent!**\n\nPlease check your other Telegram devices.\nReply with the code (e.g. `1 2 3 4 5`).")
            
        except Exception as e:
            logger.error(f"Login failed: {e}", exc_info=True)
            await status_msg.edit(f"❌ Error during login:\n`{str(e)}`\n\nPlease try /login again.")
            await user.disconnect()
            LOGIN_STATE['step'] = None
        return

    if LOGIN_STATE.get('step') == 'ask_otp' and LOGIN_STATE.get('chat_id') == chat_id:
        otp_clean = text.replace(" ", "")
        try:
            await user.sign_in(phone=LOGIN_STATE['phone'], code=otp_clean, phone_code_hash=LOGIN_STATE['hash'])
            await event.respond("✅ Login Successful!")
            LOGIN_STATE['step'] = None
        except SessionPasswordNeededError:
            LOGIN_STATE['step'] = 'ask_2fa'
            await event.respond("🔐 2FA Password Required. Please send your password:")
        except Exception as e:
            await event.respond(f"❌ Login failed: {e}")
        return

    if LOGIN_STATE.get('step') == 'ask_2fa' and LOGIN_STATE.get('chat_id') == chat_id:
        try:
            await user.sign_in(password=text)
            await event.respond("✅ 2FA Login Successful!")
            LOGIN_STATE['step'] = None
        except Exception as e:
            await event.respond(f"❌ Password failed: {e}")
        return

    # --- Link Queueing ---
    urls = re.findall(r'(https?://(?:www\.)?instagram\.com/[^\s]+)', text)
    if urls:
        count = 0
        for url in urls:
            await LINK_QUEUE.put(url)
            count += 1
        
        q_size = LINK_QUEUE.qsize()
        await event.respond(f"✅ Added {count} links to queue.\nTotal in Queue: {q_size}\n\nProcessing started.")
        
        global IS_PROCESSING
        if not IS_PROCESSING:
            asyncio.create_task(process_queue(event.chat_id))

# --- User Client Processing Logic ---

async def process_queue(notify_chat_id):
    global IS_PROCESSING
    IS_PROCESSING = True
    
    await bot.send_message(notify_chat_id, f"🚀 Batch Processing")
    
    if not user.is_connected():
        await user.connect()

    while not LINK_QUEUE.empty():
        url = await LINK_QUEUE.get()
        try:
            logger.info(f"Processing {url}")
            async with user.conversation(TARGET_PRIMARY, timeout=60) as conv:
                await conv.send_message(url)
                
                # The bot sends multiple messages:
                # 1. "Started downloading..."
                # 2. Emoji
                # 3. Final Result (Media OR Error)
                
                final_response = None
                
                # Loop to consume incoming messages until we get a result
                start_time = asyncio.get_event_loop().time()
                while (asyncio.get_event_loop().time() - start_time) < 45:
                    try:
                        response = await conv.get_response()
                    except asyncio.TimeoutError:
                        break
                    
                    if response.media:
                        final_response = response
                        break
                    
                    text_lower = response.text.lower() if response.text else ""
                    
                    # Check for known "Processing" messages to ignore
                    if "я начал качать" in text_lower or "подождите" in text_lower or "film_4k_bot" in text_lower:
                        continue
                    
                    # Check for Error Signature
                    is_error_text = False
                    for sig in ERROR_SIGNATURES:
                        if sig.lower() in text_lower:
                            is_error_text = True
                            break
                    
                    if is_error_text:
                        final_response = response
                        break
                        
                    # If it's just an emoji or very short text (spacer), ignore
                    if len(response.text) < 5:
                        continue
                        
                    # If we got here, it might be an unknown text response, treat as potential error/status
                    # But let's keep waiting in case media comes next
                    # (Unless it matches strict error, which we caught above)
                    
                # --- Decision Logic ---
                if final_response and final_response.media:
                    # ✅ Success
                    try:
                        await user.send_file(
                            GROUP_MEDIA, 
                            final_response.media, 
                            caption=f"{url}"
                        )
                        await bot.send_message(notify_chat_id, f"✅ Saved: {url}")
                    except Exception as e:
                        logger.error(f"Copy Error: {e}")
                else:
                    # ❌ Error or Timeout
                    # Send to Fallback
                    await user.send_message(TARGET_FALLBACK, url)
                    
                    # Log to Error Group
                    await user.send_message(GROUP_ERROR, f"Error\n{url}")
                    
                    await bot.send_message(notify_chat_id, f"⚠️ Error -> Fallback: {url}")

            await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Logic Error: {e}")
            
    await bot.send_message(notify_chat_id, "✅ Batch Done!")
    IS_PROCESSING = False

# --- Main Entry ---
if __name__ == '__main__':
    print("---------------------------------------")
    logger.info("Starting Reflector Bot...")
    
    # Use absolute path for session to prevent loss during generic CWD changes
    session_path = os.path.abspath("user_session")
    logger.info(f"Session File Path: {session_path}.session")
    
    loop = asyncio.get_event_loop()
    
    # Verify User Session Immediately
    async def init_clients():
        # Connect User Client
        try:
            await user.connect()
            if await user.is_user_authorized():
                me = await user.get_me()
                logger.info(f"✅ User Client Logged In as: {me.first_name} (@{me.username})")
            else:
                logger.warning("⚠️ User Client NOT Logged In. Send /login to Controller Bot.")
        except Exception as e:
            if "database is locked" in str(e):
                logger.critical("❌ FATAL ERROR: Database is locked! This means another instance of the bot is already running.")
                logger.critical("➡️ RUN: 'sudo systemctl stop extracter' AND 'pkill -f python3' to fix this.")
            logger.error(f"❌ User Client Connection Failed: {e}")

    # Run init before starting polling
    loop.run_until_complete(init_clients())
    
    # Start Controller Bot
    bot.run_until_disconnected()