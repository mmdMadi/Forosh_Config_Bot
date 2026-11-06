import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_USER_IDS = [int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()]

# 3X-UI Panel
PANEL_URL = os.getenv("PANEL_URL", "https://time.consub.online:2087")
PANEL_API_PATH = os.getenv("PANEL_API_PATH", "/QwKSHCxv7daW6W4uqS")
PANEL_USERNAME = os.getenv("PANEL_USERNAME", "admin")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "admin")
PANEL_API_TOKEN = os.getenv("PANEL_API_TOKEN", "")
