import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "8891391292:AAEnCJI5o3v3dx6hT1zqJyYLR9mfvDJTjU8")

ADMIN_ID = 5429363551

MAX_VPS_PER_USER   = 1      # regular users: max 1 VPS
DEFAULT_TOTAL_SLOTS = 10    # total VPS slots across all users

VPS_MEMORY_LIMIT = "512m"
VPS_CPU_QUOTA    = 50000
VPS_CPU_PERIOD   = 100000
VPS_IMAGE_NAME   = "vps-bot-ubuntu"
DB_PATH          = "data/vps_bot.db"
