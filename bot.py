import logging
import asyncio
import requests
import os
import sys
import io
from bs4 import BeautifulSoup
from telethon import TelegramClient, events

# Configuration
API_ID = 38659771
API_HASH = "6178147a40a23ade99f8b3a45f00e436"
BOT_TOKEN = "7966844330:AAE10tysbFmMnL3dIQhf1RHrNEwRUrpDJOU"
ALLOWED_GROUP_ID = -1003759432523

# Logger setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Telegram Client
bot = TelegramClient('insta_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

def get_instagram_media_links(instagram_url):
    """
    Takes an Instagram post URL, queries media.mollygram.com,
    and returns a list of media download URLs found in the response.
    """
    base_url = "https://media.mollygram.com/"
    params = {'url': instagram_url}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        logger.info(f"Fetching data for: {instagram_url}...")
        response = requests.get(base_url, params=params, headers=headers)
        response.raise_for_status()
        
        try:
            data = response.json()
        except Exception:
            logger.error("Error: content is not valid JSON.")
            return []

        if data.get("status") != "ok":
            logger.error(f"Error from API: {data.get('status')}")
            return []

        html_content = data.get("html", "")
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, 'html.parser')
        media_links = []
        
        # Primary target: id="download-video"
        download_buttons = soup.find_all('a', id='download-video')
        
        # Fallback: look for class 'bg-gradient-success'
        if not download_buttons:
             download_buttons = soup.find_all('a', class_='bg-gradient-success')

        for btn in download_buttons:
            href = btn.get('href')
            if href:
                media_links.append(href)

        return media_links

    except Exception as e:
        logger.error(f"Request failed: {e}")
        return []

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.chat_id != ALLOWED_GROUP_ID and not event.is_private:
        return
    await event.respond("Hello! Send me an Instagram post URL here to extract media.")

@bot.on(events.NewMessage(pattern='/update'))
async def update_handler(event):
    if event.chat_id != ALLOWED_GROUP_ID and not event.is_private:
        return

    msg = await event.reply("Checking for updates...")
    try:
        # Run git pull
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
            # Restart the script
            os.execl(sys.executable, sys.executable, *sys.argv)
            
    except Exception as e:
        await msg.edit(f"Update failed: {e}")
        logger.error(f"Update failed: {e}")

@bot.on(events.NewMessage)
async def message_handler(event):
    # Check if message is from the allowed group or DM
    if event.chat_id != ALLOWED_GROUP_ID and not event.is_private:
        return

    # Ignore commands (handled by their own handlers)
    if event.text.startswith('/'):
        return

    ctx = event.text.strip()
    
    # Check if it looks like a URL
    if "instagram.com" in ctx:
        status_msg = await event.reply("Processing... Please wait.")
        
        try:
            media_links = await asyncio.to_thread(get_instagram_media_links, ctx)
            
            # Clean the URL (remove query parameters and trailing slash)
            cleaned_url = ctx.split('?')[0].rstrip('/')

            if not media_links:
                await status_msg.edit(f"Error - No Media Found\n{cleaned_url}")
                return

            await status_msg.edit(f"Found {len(media_links)} media file(s). Uploading now...")
            
            total_media = len(media_links)
            for i, link in enumerate(media_links, 1):
                caption = f"{cleaned_url}"
                if total_media > 1:
                    caption = f"{i}/{total_media}\n{cleaned_url}"

                try:
                    # Download content to send as proper media type
                    r = await asyncio.to_thread(requests.get, link)
                    if r.status_code == 200:
                        is_video = 'video' in r.headers.get('Content-Type', '')
                        filename = 'video.mp4' if is_video else 'image.jpg'
                        
                        file_obj = io.BytesIO(r.content)
                        file_obj.name = filename
                        
                        await bot.send_file(event.chat_id, file_obj, caption=caption, force_document=False)
                    else:
                        await event.respond(f"Failed to download a file: {link}")
                except Exception as e:
                    logger.error(f"Error sending file: {e}")
                    await event.respond(f"Failed to upload a file: {link}")
            
            await status_msg.delete()
            
        except Exception as e:
            logger.error(f"Error in handler: {e}")
            await status_msg.edit("An error occurred while processing your request.")

def main():
    logger.info("Bot is running...")
    bot.run_until_disconnected()

if __name__ == '__main__':
    main()
