# Instagram Media Downloader Bot

A simple Telegram bot that takes an Instagram Post/Reel URL and sends back the media files (images/videos) contained in that post.

## Features
- Extracts media from Public Instagram posts, Reels, and Carousels.
- Sends the media directly to the Telegram chat.
- Lightweight and easy to deploy.

## Prerequisites
- Python 3.7+
- A Telegram Bot Token (from @BotFather)
- Telegram API ID and Hash (from my.telegram.org)

## Setup and Installation

1. **Clone the repository** (or copy the files):
   ```bash
   git clone https://github.com/itsprince05/extracter.git
   cd extracter
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configuration**:
   Open `bot.py` and ensure the following variables are set (already pre-filled for this specific build):
   ```python
   API_ID = 38659771
   API_HASH = "6178147a40a23ade99f8b3a45f00e436"
   BOT_TOKEN = "7966844330:AAE10tysbFmMnL3dIQhf1RHrNEwRUrpDJOU"
   ```

4. **Run the bot**:
   ```bash
   python bot.py
   ```

## Usage
1. **Join the Allowed Group or DM**: The bot works in the group `-1003759432523` or via Direct Message.
2. **Download Media**: Send an Instagram link (e.g., `https://www.instagram.com/p/Example/`) to the bot.
3. **Update Bot**: Send `/update` to pull the latest changes from the git repository and restart the bot.

## Files
- `bot.py`: Main bot logic.
- `requirements.txt`: Python dependencies.

