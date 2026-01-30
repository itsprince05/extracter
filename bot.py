import asyncio
import logging
import os
import sys
import re
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError

# --- Configuration ---
# API Credentials (ensure these are set in your VPS environment)
API_ID = 38659771
API_HASH = '6178147a40a23ade99f8b3a45f00e436'
CONTROLLER_BOT_TOKEN = "8533327762:AAHR1D4CyFpMQQ4NztXhET6OL4wL1kHNkQ4" # The new bot token you provided

# The bot you found that downloads media (You must set this!)
# Send /settarget @username to the controller bot to set it.
TARGET_BOT_USERNAME = None 

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
        "2. **/settarget @bot** - Set the downloader bot username.\n"
        "3. **Send Links** - Send Instagram links here (bulk supported).\n\n"
        "I will forward them to the target bot from your account and clean up the junk!"
    )

@bot.on(events.NewMessage(pattern='/settarget'))
async def set_target(event):
    global TARGET_BOT_USERNAME
    try:
        target = event.message.text.split(" ")[1]
        if not target.startswith("@"):
            await event.respond("Please use format: `/settarget @botusername`")
            return
        TARGET_BOT_USERNAME = target
        await event.respond(f"✅ Target Bot set to: {TARGET_BOT_USERNAME}")
    except IndexError:
        await event.respond("⚠️ Usage: `/settarget @botusername`")

@bot.on(events.NewMessage(pattern='/login'))
async def login_step1(event):
    if not event.is_private:
        await event.respond("⚠️ Security: Please send `/login` in my **Private Message (DM)** only.", link_preview=False)
        return

    chat_id = event.chat_id
    await event.respond("📱 **Login Process Initiated**\n\nPlease send your phone number in international format.\nExample: `+919876543210`")
    LOGIN_STATE['chat_id'] = chat_id
    LOGIN_STATE['step'] = 'ask_phone'

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
            # Disconnect to ensure fresh state next time
            await user.disconnect()
            LOGIN_STATE['step'] = None
        return

    if LOGIN_STATE.get('step') == 'ask_otp' and LOGIN_STATE.get('chat_id') == chat_id:
        # User sends "1 2 3 4 5", we convert to "12345"
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
    # Detect Intagram links
    urls = re.findall(r'(https?://(?:www\.)?instagram\.com/[^\s]+)', text)
    if urls:
        count = 0
        for url in urls:
            await LINK_QUEUE.put(url)
            count += 1
        
        q_size = LINK_QUEUE.qsize()
        await event.respond(f"✅ Added {count} links to queue.\nTotal in Queue: {q_size}\n\nProcessing started if target set.")
        
        global IS_PROCESSING
        if not IS_PROCESSING:
            asyncio.create_task(process_queue(event.chat_id))

# --- User Client (Interacts with Downloader Bot) ---

async def process_queue(notify_chat_id):
    global IS_PROCESSING
    IS_PROCESSING = True
    
    if not TARGET_BOT_USERNAME:
        await bot.send_message(notify_chat_id, "⚠️ **Target Bot not set!**\nUse `/settarget @username` to start.")
        IS_PROCESSING = False
        return

    await bot.send_message(notify_chat_id, f"🚀 Starting batch processing with {TARGET_BOT_USERNAME}...")
    
    while not LINK_QUEUE.empty():
        url = await LINK_QUEUE.get()
        try:
            # 1. Send Link to Target Bot
            await user.send_message(TARGET_BOT_USERNAME, url)
            logger.info(f"Sent {url} to {TARGET_BOT_USERNAME}")
            
            # 2. Wait for cooldown (User requested 10 seconds)
            await asyncio.sleep(10) 
            
        except Exception as e:
            await bot.send_message(notify_chat_id, f"❌ Error processing link: {e}")
            await asyncio.sleep(5)

    await bot.send_message(notify_chat_id, "✅ Batch processing complete!")
    IS_PROCESSING = False

# --- Garbage Collector (User Client) ---
# Listens to messages FROM the Target Bot
@user.on(events.NewMessage())
async def handle_target_response(event):
    if not TARGET_BOT_USERNAME: return
    
    # Check if message is from the target bot
    sender = await event.get_sender()
    # Handle username vs ID matching robustly
    try:
        if sender.username and ("@" + sender.username).lower() == TARGET_BOT_USERNAME.lower():
            is_target = True
        else:
            is_target = False
    except:
        is_target = False

        # User requested to NOT delete messages ("koi bhi msg delete mar karo" -> delete mat karo)
        # if event.message.media:
        #    pass
        # else:
        #    # Text message (ads, "processing", "join channel", etc)
        #    try:
        #        await event.delete()
        #        logger.info("Deleted garbage text message.")
        #    except Exception as e:
        #        logger.error(f"Failed to delete garbage: {e}")
        pass

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
            logger.error(f"❌ User Client Connection Failed: {e}")

    # Run init before starting polling
    loop.run_until_complete(init_clients())
    
    # Start Controller Bot
    bot.run_until_disconnected()