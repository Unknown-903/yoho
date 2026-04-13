# ══════════════════════════════════════════════════════
#              MERGED BOT - config_sample.py
#   Copy to config.py and fill your values
# ══════════════════════════════════════════════════════

# ── Core Telegram ──────────────────────────────────────
API_ID           = 0              # From my.telegram.org
API_HASH         = ""             # From my.telegram.org
BOT_TOKEN        = ""             # From @BotFather
OWNER_ID         = 0              # Your Telegram user ID
SUDO_USERS       = ""             # Space-separated user IDs
AUTHORIZED_CHATS = ""             # Space-separated chat IDs

# ── Database ───────────────────────────────────────────
DATABASE_URL     = ""             # MongoDB connection string

# ── Channels ───────────────────────────────────────────
LOG_CHAT_ID      = 0              # Log channel ID (negative)
LEECH_DUMP_CHAT  = []             # Dump channel(s) for leeched files

# ── Leech Settings ─────────────────────────────────────
LEECH_SPLIT_SIZE = 2097152000     # 2GB default
AS_DOCUMENT      = False          # Send as document by default
MEDIA_GROUP      = False          # Group media files
USER_SESSION_STRING = ""          # Pyrogram user session (optional)
USER_TRANSMISSION   = False       # Use user session for upload

# ── Clone / GDrive ─────────────────────────────────────
GDRIVE_ID        = ""             # Root GDrive folder ID
USE_SERVICE_ACCOUNTS = False      # Use service accounts
INDEX_URL        = ""             # GDrive index URL

# ── RSS ────────────────────────────────────────────────
RSS_CHAT         = ""             # RSS update channel/group
RSS_DELAY        = 600            # Seconds between RSS checks
RSS_SIZE_LIMIT   = 0              # Max size in bytes (0 = unlimited)

# ── Search ─────────────────────────────────────────────
# (Torrent search — keep HYDRA for NZB search if needed)
HYDRA_IP         = ""
HYDRA_API_KEY    = ""

# ── Queue ──────────────────────────────────────────────
QUEUE_ALL        = 0              # 0 = unlimited concurrent tasks
QUEUE_DOWNLOAD   = 0
QUEUE_UPLOAD     = 0

# ── Bot Behaviour ──────────────────────────────────────
SET_COMMANDS     = True           # Auto-set bot commands
DELETE_LINKS     = False          # Delete command messages
INCOMPLETE_TASK_NOTIFIER = False

# ── Media Tools (from Multi-Task-bot) ──────────────────
DOWNLOAD_DIR     = "/app/downloads/"
ENCODE_DIR       = "/app/encodes/"
LOG_CHANNEL      = 0              # Same as LOG_CHAT_ID (legacy compat)
DUMP_CHANNEL     = 0              # Dump channel for processed files
ADMIN_URL        = ""             # Admin contact URL
FSUB_IDS         = ""             # Force-subscribe channel IDs

# ── Misc ───────────────────────────────────────────────
UPSTREAM_REPO    = "https://github.com/yourusername/merged-bot"
UPSTREAM_BRANCH  = "main"
BASE_URL         = ""             # For web server (Heroku etc.)
BASE_URL_PORT    = 80
WEB_PINCODE      = False
TOKEN_TIMEOUT    = 0
PAID_CHANNEL_ID  = 0
PAID_CHANNEL_LINK= ""
NAME_PREFIX      = ""
LEECH_FILENAME_CAPTION = ""
METADATA_KEY     = ""
WATERMARK_KEY    = ""
THUMBNAIL_LAYOUT = ""
INSTADL_API      = ""
STREAMWISH_API   = ""
FILELION_API     = ""
STOP_DUPLICATE   = False
