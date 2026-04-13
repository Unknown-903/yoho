import os
import re
import time
import asyncio
import logging
import math
import html
import datetime
import pytz

from bot.modules.media_tools.client_compat import Client
from pyrogram import filters, enums, ContinuePropagation
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from bot.helper.media_helper.database import codeflixbots
from bot.helper.media_helper.auth import auth_chats
from bot.helper.media_helper.permissions import is_owner, is_admin as _perm_is_admin, is_authorized_chat, can_access_premium_feature
from bot.helper.media_helper.utils import humanbytes, TimeFormatter, safe_edit, apply_replacor, build_metadata_args, check_watermark_for_process
from lang_map import get_original_title, LANGUAGE_MAP
from bot.core.config_manager import Config
from bot.helper.media_helper.command_lock import acquire_lock, release_lock, is_locked
from bot.helper.media_helper.cleanup import cleanup_task, safe_delete_files
from bot.helper.media_helper.audio_reorder import probe_and_reorder_audio, build_audio_map_args

import sys
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

file_queue = asyncio.Queue()
processing = False

queue_users = {}
current_user = None

cancel_tasks = {}      # task_token -> True (per-task cancel, not per-user)
current_task_info = {}  # {"filename": "...", "stage": "..."}
task_owner_map = {}    # task_token -> user_id (server-side verification)
select_sessions = {}   # user_id -> session dict
SESSION_TIMEOUT = 6 * 3600  # 6 hours auto-expiry

# ================= BATCH RENAME STATE =================

batch_rename_state = {}  # user_id -> {"files": [messages], "settings": {...}}

_last_edit_times = {}  # msg_id -> last progress edit timestamp


def build_watermark_filter(wm_text, wm_position, wm_size, wm_opacity, wm_color="white", wm_style="shadow"):
    """Build FFmpeg drawtext filter for watermark."""
    preset_sizes = {"small": "18", "medium": "28", "large": "42", "xlarge": "56"}
    fontsize_expr = preset_sizes.get(wm_size, "28")
    positions = {
        "top_left": "x=20:y=20", "top_right": "x=w-tw-20:y=20",
        "bottom_left": "x=20:y=h-th-20", "bottom_right": "x=w-tw-20:y=h-th-20",
        "center": "x=(w-tw)/2:y=(h-th)/2",
    }
    pos = positions.get(wm_position, "x=w-tw-20:y=20")
    safe_text = wm_text.replace("'", "\\'").replace(":", "\\:")
    alpha = max(0.1, min(1.0, float(wm_opacity or 0.7)))
    color = wm_color if wm_color else "white"
    if wm_style == "shadow":
        effects = f"shadowcolor=black@{alpha*0.8}:shadowx=2:shadowy=2:borderw=1:bordercolor=black@{alpha*0.5}"
    elif wm_style == "outline":
        effects = f"borderw=3:bordercolor=black@{alpha}"
    elif wm_style == "bold":
        effects = f"borderw=4:bordercolor=black@{alpha}:shadowcolor=black@{alpha}:shadowx=3:shadowy=3"
    else:
        effects = ""
    base = f"drawtext=text=\'{safe_text}\':fontsize={fontsize_expr}:{pos}:fontcolor={color}@{alpha}"
    if effects:
        base += f":{effects}"
    return base

# ================= ADMIN CHECK =================

def _is_admin_rename(user_id):
    return _perm_is_admin(user_id)


# ================= ADMIN COMMANDS =================

@Client.on_message((filters.private | filters.group) & filters.command("add"))
async def add_admin(client, message):
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply_text("❌ **Only the owner can use this command.**")

    if not message.reply_to_message:
        return await message.reply_text("❌ **Reply to a user to add them as admin.**")

    new_admin = message.reply_to_message.from_user.id

    if new_admin == Config.OWNER_ID:
        return await message.reply_text("❌ **Owner is already the owner.**")

    if new_admin in Config.ADMIN:
        return await message.reply_text("⚠️ **This user is already an admin.**")

    Config.ADMIN.append(new_admin)
    await message.reply_text(
        f"✅ **Admin Added**\n\n"
        f"👤 **User ID:** `{new_admin}`"
    )


@Client.on_message((filters.private | filters.group) & filters.command("rm"))
async def remove_admin(client, message):
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply_text("❌ **Only the owner can use this command.**")

    if not message.reply_to_message:
        return await message.reply_text("❌ **Reply to an admin to remove them.**")

    user_id = message.reply_to_message.from_user.id

    if user_id == Config.OWNER_ID:
        return await message.reply_text("❌ **Cannot remove the owner.**")

    if user_id in Config.ADMIN:
        Config.ADMIN.remove(user_id)
        await message.reply_text(
            f"✅ **Admin Removed**\n\n"
            f"👤 **User ID:** `{user_id}`"
        )
    else:
        await message.reply_text("⚠️ **This user is not an admin.**")


@Client.on_message((filters.private | filters.group) & filters.command("addlist"))
async def admin_list(client, message):
    if message.from_user.id != Config.OWNER_ID:
        return await message.reply_text("❌ **Only the owner can use this command.**")

    text = "👑 **Admin List**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"👑 **Owner:** `{Config.OWNER_ID}`\n\n"

    if Config.ADMIN:
        for i, admin in enumerate(Config.ADMIN, 1):
            text += f"  {i}. `{admin}`\n"
    else:
        text += "  _No admins added yet._"

    await message.reply_text(text)



# ================= REGEX =================

SEASON_EPISODE_PATTERN = re.compile(
    r"(?:"
    # S01E01, Season 1 Episode 1, Series 01 Ep 05
    r"(?:S|Season|Series)[ ._\-]?(\d{1,3})[ ._\-]?(?:E|Episode|Ep)[ ._\-]?(\d{1,3})"
    r"|"
    # S01-E05 (dash-separated)
    r"[Ss](\d{1,3})\s*[-]\s*[Ee](\d{1,3})"
    r"|"
    # [S01E01] or (S01E01) — bracket-enclosed
    r"[\[\(][Ss](\d{1,3})[Ee](\d{1,3})[\]\)]"
    r"|"
    # S01 01 (space-separated season episode)
    r"[Ss](\d{1,3})[ ._\-]+(\d{1,3})"
    r"|"
    # 1x01 format
    r"(\d{1,3})x(\d{1,3})"
    r"|"
    # Ep01 / E01 / EP 01 (uppercase too)
    r"[Ee][Pp]?[ ._\-]?(\d{1,3})"
    r"|"
    # Episode 01 / Episode - 01
    r"[Ee]pisode[ ._\-]?(\d{1,3})"
    r"|"
    # - 01 (common anime pattern, must follow a word boundary)
    r"(?<=\s)[-–][ ]?(\d{2,3})(?=\s|$|[.\[\(])"
    r"|"
    # Part 01, Vol 03
    r"(?:Part|Pt|Vol|Volume)[ ._\-]?(\d{1,3})"
    r")",
    re.IGNORECASE
)

QUALITY_PATTERN = re.compile(
    r"(4[Kk]|8[Kk]|\d{3,4}[pP])", re.IGNORECASE
)

CODEC_PATTERN = re.compile(
    r"(HEVC|H\.?265|x265|H\.?264|x264|AVC|AV1|VP9|OPUS|AAC|FLAC|DDP?(?:5\.1)?|10[- ]?bit|HDR(?:10)?)",
    re.IGNORECASE
)

# Audio detection: full names match freely, short codes need word boundaries
_AUDIO_FULL_NAMES = sorted(set(LANGUAGE_MAP.values()), key=len, reverse=True)
_AUDIO_SHORT_CODES = ["Eng", "Hin", "Jpn", "Tam", "Tel", "Kor", "Mal", "Kan", "Mar", "Ben", "Guj", "Pun"]
_full_pat = "|".join(re.escape(kw) for kw in _AUDIO_FULL_NAMES)
_short_pat = "|".join(r"(?<![A-Za-z])" + re.escape(kw) + r"(?![A-Za-z])" for kw in _AUDIO_SHORT_CODES)
AUDIO_PATTERN = re.compile(
    r"(\d[ ._\-]?Audio|Dual[ ._\-]?Audio|Multi[ ._\-]?Audio|" + _full_pat + "|" + _short_pat + r")",
    re.IGNORECASE
)

VIDEO_CODEC_PATTERN = re.compile(
    r"(HEVC|H\.?265|x265|H\.?264|x264|AVC|AV1|VP9|VP8|VVC)",
    re.IGNORECASE
)

AUDIO_CODEC_PATTERN = re.compile(
    r"(AAC|AC3|DDP(?:5\.1)?|OPUS|MP3|FLAC|DTS|EAC3|WAV|PCM|VORBIS|ALAC)",
    re.IGNORECASE
)

YEAR_PATTERN = re.compile(
    r"(?:^|[\s\(\[\-])(\d{4})(?:[\s\)\]\-]|$)"
)




# ================= SEASON/EPISODE EXTRACTION (OVERHAULED) =================

def extract_season_episode(filename):
    """Extract season and episode using prioritized pattern list.
    Returns (season, episode) as zero-padded strings or (None, None).
    """
    if not filename:
        return None, None
    name = filename

    _PATTERNS = [
        (r"[Ss](\d{1,3})\s*[Ee](\d{1,4})(?:v\d)?", True),
        (r"(?:Season|Series)\s*(\d{1,3})\s*(?:Episode|Ep\.?)\s*(\d{1,4})", True),
        (r"[\[\(]\s*[Ss](\d{1,3})[Ee](\d{1,4})\s*[\]\)]", True),
        (r"[Ss](\d{1,3})\s*[-]\s*[Ee](\d{1,4})", True),
        (r"(\d{1,3})[Xx](\d{1,4})", True),
        (r"(?:Episode|Ep\.?)\s*[-]?\s*(\d{1,4})", False),
        (r"(?<=[\s._\[\(-])[Ee](\d{2,4})(?=[\s._\]\)\-]|$)", False),
        (r"#(\d{1,4})", False),
        (r"(?<=[\s\]])[-\u2013\u2014]\s*(\d{2,4})(?=[\s._\[\(]|$)", False),
        (r"(?:Chapter|Ch\.?)\s*(\d{1,4})", False),
        (r"(?:Part|Pt\.?|Vol\.?|Volume)\s*(\d{1,4})", False),
        (r"(?:OVA|OAD|Special|SP|ONA)\s*(\d{1,3})", False),
        (r"(?:^|[\s._\-])(?<!\d)(\d{2,3})(?:v\d)?(?=[\s._\-\[\(]|$)", False),
    ]

    for pattern, has_season in _PATTERNS:
        m = re.search(pattern, name, re.IGNORECASE)
        if m:
            groups = m.groups()
            if has_season and len(groups) >= 2 and groups[0] and groups[1]:
                return groups[0].zfill(2), groups[1].zfill(2)
            elif not has_season and groups[0]:
                return "01", groups[0].zfill(2)
    return None, None


SOURCE_PATTERN = re.compile(
    r"(WEB[-. ]?DL|WEB[-. ]?Rip|Blu[-. ]?Ray|BDRip|BRRip|DVDRip|HDRip|HDTV|PDTV|HDR|SDR|"
    r"AMZN|NF|MX|MXP|HULU|TUBI|TUBITV|PLUTOTV|SHEMAROO|XSTREAM|DSNP|HMAX|ATVP|PCOK|HULU|APTV|Netflix|Amazon|Disney\+?|Crunchyroll|Funimation|CR|TPLAY|JIOTV\+?|HBO\s?Max|Apple\s?TV|Prime\s?Video|Google\s?Play|iTunes|MANGOMAN\s?TV|HBOGO|HBO\s?Now|Vudu|Flixtor|CineBloom|RARBG|YTS|EVO|Ganool|Shana|YIFY|RARBG|ETRG|FGT|MkvCage|GOM[-. ]?TV|Kiss[-. ]?Anime|Anime[-. ]?Pahe|Sukebei|Nyaa|Tokyo[-. ]?Tosho|AniDex|AnimeBytes|BakaBT|SABnzbd|NZBGeek|NZBFinder(?:-Elite)?|NZBPlanet|BinSearch|NZBIndex|OZnzb|DrunkenSlug|NZBKingdom|NZBFriends|NZBStars|NZBCat(?:elog)?|NZB(?:Xtreme)?(?:Search)?|Newz(?:leech)?(?:Pro)?|(?:HD)?(?:T)?V[-. ]?Release(?:Group)?)(?:\s?Group)?",
    re.IGNORECASE
)

# ================= REPLACOR =================

# ================= EXTRACT HELPERS =================

def generate_thumb_at_midpoint(file_path, output_thumb, duration=None):
    """Generate a thumbnail at 50% of the video duration using ffmpeg."""
    import subprocess
    try:
        if duration is None:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", file_path],
                capture_output=True, text=True, timeout=30)
            duration = float(result.stdout.strip()) if result.stdout.strip() else None
        if not duration or duration <= 0:
            logger.info(f"Thumb skipped: no duration for {os.path.basename(file_path)}")
            return None
        midpoint = duration / 2
        # Try with scale filter first; fallback without if it fails (odd dimensions)
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(midpoint), "-i", file_path,
             "-vframes", "1", "-q:v", "2", "-vf", "scale=320:-2", output_thumb],
            capture_output=True, timeout=30)
        if result.returncode != 0:
            # Retry without scale filter for problematic files
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(midpoint), "-i", file_path,
                 "-vframes", "1", "-q:v", "2", output_thumb],
                capture_output=True, timeout=30)
        if os.path.exists(output_thumb) and os.path.getsize(output_thumb) > 0:
            return output_thumb
        logger.info(f"Thumb generation produced empty file for {os.path.basename(file_path)}")
        return None
    except Exception as e:
        logger.info(f"Thumb generation failed for {os.path.basename(file_path)}: {e}")
        return None

async def progress_for_pyrogram(current, total, ud_type, message, start, task_token=None):
    now = time.time()
    diff = now - start
    if diff <= 0:
        return

    # Check if task was cancelled
    if task_token and cancel_tasks.get(task_token):
        return

    # Throttle: edit every 7 seconds
    msg_id = message.id
    last = _last_edit_times.get(msg_id, 0)
    if now - last < 7 and current != total:
        return
    _last_edit_times[msg_id] = now

    percentage = min(max(current * 100 / total, 0), 100) if total else 0
    speed = current / diff if diff else 0
    eta = (total - current) / speed if (speed and total and total > current) else 0
    elapsed = TimeFormatter(int(diff * 1000))
    eta_text = TimeFormatter(int(eta * 1000)) if eta else "--"

    # Aeon-style progress bar
    c_full = int((percentage + 5) // 10)
    p_str = "●" * c_full + "○" * (10 - c_full)
    eta_str = TimeFormatter(int(eta * 1000)) if eta else "-"

    text = (
        f"<b>{ud_type}</b>\n"
        f"{p_str} {round(percentage, 2)}%\n"
        f"<b>Processed:</b> {humanbytes(current)}/{humanbytes(total)}\n"
        f"<b>Speed:</b> {humanbytes(speed)}/s\n"
        f"<b>Estimated:</b> {eta_str}"
    )

    try:
        await message.edit(text, parse_mode="html")
        if current == total:
            _last_edit_times.pop(msg_id, None)
    except FloodWait as e:
        _last_edit_times[msg_id] = now + e.value
    except Exception:
        pass


# ================= HELPERS =================

def extract_source(filename):
    """Extract source/release info (WEB-DL, BluRay, AMZN, etc)."""
    if not filename:
        return ""
    match = SOURCE_PATTERN.search(filename)
    return match.group(1) if match else ""

def extract_quality(filename):
    match = QUALITY_PATTERN.search(filename)
    return match.group(1) if match else "Unknown"


def extract_codec(filename):
    matches = CODEC_PATTERN.findall(filename)
    return " ".join(matches) if matches else ""


def extract_vcodec(filename):
    matches = VIDEO_CODEC_PATTERN.findall(filename)
    return " ".join(dict.fromkeys(matches)) if matches else ""


def extract_acodec(filename):
    matches = AUDIO_CODEC_PATTERN.findall(filename)
    return " ".join(dict.fromkeys(matches)) if matches else ""


def extract_audio(filename):
    matches = AUDIO_PATTERN.findall(filename)
    return " ".join(dict.fromkeys(matches)) if matches else ""


def extract_subs(filename):
    if not filename:
        return ""
    lower = filename.lower()
    if re.search(r"\b(msubs|multi[ ._\-]*subs?|multisubs?)\b", lower):
        return "MSubs"
    if re.search(r"\b(esubs|english[ ._\-]*subs?|eng[ ._\-]*subs?)\b", lower):
        return "ESubs"
    if re.search(r"\b(subs?|subbed|subtitles?)\b", lower):
        return "SingleSubs"
    return ""


def extract_year(filename):
    match = YEAR_PATTERN.search(filename)
    if match:
        year = int(match.group(1))
        if 1950 <= year <= 2099:
            return str(year)
    return ""


def format_caption(text, style="regular"):
    text = text or ""
    safe = html.escape(text)

    styles = {
        "original": safe,
        "regular": safe,
        "bold": f"<b>{safe}</b>",
        "italic": f"<i>{safe}</i>",
        "underline": f"<u>{safe}</u>",
        "quote": "\n".join("> " + line for line in (safe.splitlines() or [safe])),
        "terminal": f"<pre>{safe}</pre>",
        "monospace": f"<code>{safe}</code>",
        "strikethrough": f"<s>{safe}</s>",
        "spoiler": f"<tg-spoiler>{safe}</tg-spoiler>",
    }
    return styles.get(style, safe)


def sanitize_filename(filename):
    filename = os.path.basename(filename or "")
    name, ext = os.path.splitext(filename)
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*]', ' ', name)
    name = re.sub(r"[^A-Za-z0-9 ._+\-\[\]\(\)&@]+", " ", name)
    name = re.sub(r"\.{2,}", ".", name)
    name = re.sub(r"_{2,}", "_", name)
    name = re.sub(r"\s{2,}", " ", name)
    name = re.sub(r"\s*\.\s+", " ", name)
    name = name.strip("_. ")
    if not name:
        name = "file"
    return f"{name}{ext}"


async def cleanup_files(*paths):
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def _cleanup_expired_sessions():
    """Remove select sessions older than SESSION_TIMEOUT."""
    now = time.time()
    expired = [
        uid for uid, s in select_sessions.items()
        if now - s.get("created_at", 0) > SESSION_TIMEOUT
    ]
    for uid in expired:
        del select_sessions[uid]
        logger.info(f"Session expired for user {uid}")


# ================= SELECT =================

@Client.on_message((filters.private | filters.group) & filters.command("select"))
async def select_range(client, message):

    # Check premium access (admin or premium user)
    if not await can_access_premium_feature(message.from_user.id):
        return await message.reply_text(
            "❌ **Premium Feature**\n\n"
            "Renaming is available for:\n"
            "✅ Admin/Owner\n"
            "✅ Premium Members\n\n"
            "📞 Contact @SharkToonsIndia for premium access!"
        )

    try:
        args = message.text.split()[1]
        start, end = map(int, args.split("-"))

        if start < 1 or end < start:
            return await message.reply_text(
                "❌ **Invalid range**\n\n"
                "**Example:** `/select 1-12`"
            )

        select_sessions[message.from_user.id] = {
            "start": start,
            "end": end,
            "count": 0,
            "created_at": time.time(),
        }

        await message.reply_text(
            f"✅ **Rename Range Set**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 **Start:** `{start}`\n"
            f"📌 **End:** `{end}`\n"
            f"📦 **Total Files:** `{end - start + 1}`\n\n"
            f"📤 _Now send your files!_\n\n"
            f"💡 Use `/clearselect` to cancel."
        )

    except (IndexError, ValueError):
        await message.reply_text(
            "❌ **Wrong format**\n\n"
            "**Usage:** `/select 1-12`\n"
            "**Example:** `/select 3-8` — renames files 3 to 8"
        )


@Client.on_message((filters.private | filters.group) & filters.command("clearselect"))
async def clear_select(client, message):
    user_id = message.from_user.id
    if user_id in select_sessions:
        del select_sessions[user_id]
        await message.reply_text("✅ **Selection cancelled!**")
    else:
        await message.reply_text("❌ **No active selection to cancel.**")


@Client.on_message((filters.private | filters.group) & filters.command("rename") & ~filters.reply)
async def batch_rename_cmd(client, message):
    user_id = message.from_user.id
    
    # Check premium access (admin or premium user)
    if not await can_access_premium_feature(user_id):
        contact = Config.ADMIN_URL or "the bot owner"
        return await message.reply_text(
            "❌ **Premium Feature**\n\n"
            "Renaming is available for:\n"
            "✅ Admin/Owner\n"
            "✅ Premium Members\n\n"
            f"📞 Contact {contact} for premium access!"
        )
    
    if message.chat.type in ["group", "supergroup"]:
        if not is_authorized_chat(message.chat.id):
            return await message.reply_text("❌ This group is not authorized.")
    
    # Initialize batch state
    batch_rename_state[user_id] = {"files": [], "settings": None, "waiting": True}
    
    await message.reply_text(
        "📥 **Batch Rename Mode**\n\n"
        "Send me multiple files, then send `/rdone` to start renaming.\n"
        "All files will use the same rename format.\n\n"
        "Files collected: 0"
    )

@Client.on_message((filters.private | filters.group) & filters.command("rdone"))
async def process_batch_rename(client, message):
    user_id = message.from_user.id
    if not _is_admin_rename(user_id):
        await message.reply_text("❌ Only admins can use batch processing.")
        return
    if message.chat.type in ["group", "supergroup"]:
        if not is_authorized_chat(message.chat.id):
            await message.reply_text("❌ This group is not authorized for batch processing.")
            return
    
    if user_id not in batch_rename_state:
        await message.reply_text("❌ No batch rename session active. Send /rename first.")
        return  # Silently return if not in rename batch mode
    
    state = batch_rename_state[user_id]
    files = state.get("files", [])
    if not files:
        return
    
    # Check if rename format is set
    rename_format = await codeflixbots.get_format_template(user_id)
    if not rename_format:
        await message.reply_text(
            "⚠️ **No rename format set!**\n\n"
            "Use `/autorename` to set your template first."
        )
        return
    
    # Process all files
    tasks_added = []
    for i, msg in enumerate(files):
        task_token = f"{user_id}_{int(time.time() * 1000)}_{i}"
        task_owner_map[task_token] = user_id
        cancel_tasks[task_token] = False
        tasks_added.append(task_token)
        
        user = message.from_user.first_name
        queue_users[user] = queue_users.get(user, 0) + 1
        
        await file_queue.put((client, msg, task_token))
    
    file_count = len(tasks_added)
    await message.reply_text(
        f"📥 **Added {file_count} files to Rename Queue**\n\n"
        f"📍 Position: {file_queue.qsize() - file_count + 1}-{file_queue.qsize()}"
    )
    batch_rename_state.pop(user_id, None)  # Clean up batch state
    asyncio.create_task(process_queue())

@Client.on_message(
    (filters.private | filters.group) & (filters.document | filters.video | filters.audio),
    group=2
)
async def batch_rename_file_handler(client, message):
    if message.text and message.text.startswith('/'):
        raise ContinuePropagation
    user_id = message.from_user.id if message.from_user else None
    if not user_id or user_id not in batch_rename_state or not batch_rename_state[user_id].get("waiting"):
        raise ContinuePropagation
    
    # Add file to batch
    state = batch_rename_state[user_id]
    state["files"].append(message)


# ================= QUEUE =================

@Client.on_message((filters.private | filters.group) & filters.command("queue"))
async def show_queue(client, message):

    text = "📦 **Rename Queue**\n━━━━━━━━━━━━━━━━━━━━\n\n"

    if current_user:
        text += f"⚙️ **Processing:** `{current_user}`\n"
        if current_task_info:
            fname = current_task_info.get("filename", "")
            stage = current_task_info.get("stage", "")
            if fname:
                text += f"  📄 **File:** `{fname[:60]}`\n"
            if stage:
                text += f"  📍 **Stage:** {stage}\n"
        text += "\n"

    if not queue_users:
        if not current_user:
            text += "💤 _Queue is empty._"
    else:
        text += "👥 **Queued Users:**\n"
        for i, (user, count) in enumerate(queue_users.items(), 1):
            text += f"  {i}. 👤 `{user}` — **{count}** file(s)\n"

    total_pending = file_queue.qsize()
    if total_pending:
        text += f"\n📊 **Total Pending:** `{total_pending}`"

    await message.reply_text(text)


# ================= HANDLE FILE =================

@Client.on_message(
    (filters.private | filters.group)
    & (filters.document | filters.video | filters.audio)
    & ~filters.command(["encode", "autorename", "sequence", "done"]),
    group=1
)
async def handle_files(client, message):

    user_id = message.from_user.id if message.from_user else None
    if not user_id:
        return

    # Group auth check
    if message.chat.type in ["group", "supergroup"]:
        if not is_authorized_chat(message.chat.id):
            return

    if not _is_admin_rename(user_id):
        return

    # Cleanup expired sessions periodically
    _cleanup_expired_sessions()

    if user_id not in select_sessions:
        return

    session = select_sessions[user_id]
    session["count"] += 1

    if session["count"] < session["start"]:
        return

    if session["count"] > session["end"]:
        del select_sessions[user_id]
        await message.reply_text(
            "✅ **Select range completed!**\n"
            f"📦 All `{session['end'] - session['start'] + 1}` files are queued."
        )
        return

    user = message.from_user.first_name
    queue_users[user] = queue_users.get(user, 0) + 1
    position = file_queue.qsize() + 1

    await message.reply_text(
        f"📥 **Added to Queue**\n\n"
        f"👤 **User:** {message.from_user.mention}\n"
        f"📍 **Position:** `{position}`\n"
        f"📊 **File** `{session['count'] - session['start'] + 1}` of `{session['end'] - session['start'] + 1}`"
    )

    await file_queue.put((client, message))
    asyncio.create_task(process_queue())


# ================= PROCESS QUEUE =================

async def process_queue():

    global processing, current_user

    if processing:
        return

    processing = True

    while not file_queue.empty():

        item = await file_queue.get()
        if len(item) == 3:
            client, message, task_token = item
        else:
            client, message = item
            task_token = None
        
        current_user = message.from_user.first_name

        try:
            await auto_rename_files(client, message, task_token)
        except Exception as e:
            logger.error(f"Rename error: {e}", exc_info=True)
            try:
                await message.reply_text(f"❌ **Error:** `{str(e)[:200]}`")
            except Exception:
                pass

        user = message.from_user.first_name
        if user in queue_users:
            queue_users[user] -= 1
            if queue_users[user] <= 0:
                del queue_users[user]

        file_queue.task_done()

    current_user = None
    processing = False


# ================= CANCEL =================

@Client.on_callback_query(filters.regex("^cancel_"))
async def cancel_task_rename(client, query):

    token = query.data
    caller_id = query.from_user.id

    owner_id = task_owner_map.get(token)

    if owner_id is None:
        return await query.answer("❌ Task not found or already done.", show_alert=True)

    # Task owner or bot owner can cancel
    if caller_id == owner_id or caller_id == Config.OWNER_ID:
        cancel_tasks[token] = True
        await query.answer("✅ Cancel request sent.")
    else:
        await query.answer("❌ This is not your task!", show_alert=True)


# ================= RESTART COMMAND =================

@Client.on_message((filters.private | filters.group) & filters.command("restart"))
async def restart_bot(client, message):
    user_id = message.from_user.id

    if not _is_admin_rename(user_id):
        return await message.reply_text("❌ **Only owner and admins can restart.**")

    # Get restart details
    ist = pytz.timezone('Asia/Kolkata')
    restart_time = datetime.datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S IST')
    restarted_by = message.from_user.mention if message.from_user.username else f"User {user_id}"
    bot_username = (await client.get_me()).username or "Unknown"

    # Send notification to support chat
    try:
        await client.send_message(
            Config.SUPPORT_CHAT,
            f"🔄 **Bot Restarted Successfully!**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 **Date & Time:** `{restart_time}`\n"
            f"👤 **Restarted By:** {restarted_by}\n"
            f"🤖 **Bot Username:** @{bot_username}\n\n"
            f"✅ **Status:** Online and ready!\n"
            f"📦 **Repository:** [GitHub](https://github.com/sujiop56/Auto-Everything)"
        )
    except Exception as e:
        logger.error(f"Failed to send restart notification: {e}")

    await message.reply_text("🔄 **Restarting bot...**")
    logger.info(f"Restart triggered by user {user_id}")

    import sys
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ================= LOGS COMMAND =================

class TelegramLogHandler(logging.Handler):
    """Stores log messages in a ring buffer."""
    def __init__(self, maxlen=300):
        super().__init__()
        self._buffer = []
        self._maxlen = maxlen
        self._client = None
        self._target = None
        self._active = False

    def setup(self, client, target):
        self._client = client
        self._target = target
        self._active = True

    def stop(self):
        self._active = False
        self._client = None
        self._target = None

    def emit(self, record):
        try:
            msg = self.format(record)
            self._buffer.append(msg)
            if len(self._buffer) > self._maxlen:
                self._buffer = self._buffer[-self._maxlen:]
        except Exception:
            pass


telegram_log_handler = TelegramLogHandler()
telegram_log_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
)
logging.getLogger().addHandler(telegram_log_handler)


@Client.on_message((filters.private | filters.group) & filters.command("logs"))
async def send_logs(client, message):

    user_id = message.from_user.id

    if not _is_admin_rename(user_id):
        return await message.reply_text("❌ **Only admins and owner can use this command.**")

    args = message.text.split()
    sub = args[1].lower() if len(args) > 1 else ""

    if sub == "stop":
        if telegram_log_handler._active:
            telegram_log_handler.stop()
            await message.reply_text("🔕 **Log streaming stopped.**")
        else:
            await message.reply_text("ℹ️ **Log streaming is not active.**")
        return

    if sub == "stream":
        if telegram_log_handler._active:
            await message.reply_text("ℹ️ **Already streaming logs to this chat.**")
            return
        telegram_log_handler.setup(client, message.chat.id)
        await message.reply_text(
            "📡 **Log Streaming Started**\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Logs will be sent here in real-time.\n"
            "Use `/logs stop` to stop streaming."
        )
        asyncio.create_task(_send_log_buffer(client, message.chat.id))
        return

    # Show recent buffered logs
    if telegram_log_handler._buffer:
        lines = telegram_log_handler._buffer[-30:]
        text = "📋 **Recent Logs**\n━━━━━━━━━━━━━━━━━━━━\n\n```\n" + "\n".join(lines) + "\n```"
        if len(text) > 4000:
            lines = telegram_log_handler._buffer[-10:]
            text = "📋 **Recent Logs**\n━━━━━━━━━━━━━━━━━━━━\n\n```\n" + "\n".join(lines) + "\n```"
        await message.reply_text(text)
    else:
        await message.reply_text(
            "📋 **No logs yet.**\n\n"
            "Use `/logs stream` to start live streaming."
        )


async def _send_log_buffer(client, chat_id):
    """Background task — sends new logs to Telegram."""
    sent_count = len(telegram_log_handler._buffer)

    while telegram_log_handler._active:
        await asyncio.sleep(8)

        current_len = len(telegram_log_handler._buffer)
        if current_len <= sent_count:
            continue

        new_logs = telegram_log_handler._buffer[sent_count:current_len]
        sent_count = current_len

        text = "\n".join(new_logs)
        if len(text) > 3800:
            text = text[-3800:]

        try:
            await client.send_message(chat_id, f"📡 **Live Logs**\n\n```\n{text}\n```")
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception:
            pass


# ================= MAIN RENAME =================

async def auto_rename_files(client, message: Message, task_token=None):

    user_id = message.from_user.id
    chat_id = message.chat.id  # send to same chat, not user DM

    progress_msg = await message.reply_text("🔄 **Starting rename...**")

    format_template = await codeflixbots.get_format_template(user_id)

    if not format_template:
        return await progress_msg.edit_text(
            "⚠️ **No rename format set!**\n\n"
            "Use `/autorename` to set your template first."
        )

    file = message.document or message.video or message.audio

    file_name = file.file_name or f"unnamed_{int(time.time())}"
    
    # Apply replacor if enabled
    replacor_enabled = await codeflixbots.get_replacor_enabled(user_id)
    if replacor_enabled:
        r_strings = await codeflixbots.get_replacor_strings(user_id)
        r_final = await codeflixbots.get_replacor_final(user_id)
        if r_strings and r_final:
            file_name = apply_replacor(file_name, r_strings, r_final)
    
    safe_original_filename = sanitize_filename(file_name)
    filename_with_ext = safe_original_filename
    filename_no_ext = os.path.splitext(safe_original_filename)[0]
    season, episode = extract_season_episode(file_name)
    quality = extract_quality(file_name)
    vcodec = extract_vcodec(file_name)
    acodec = extract_acodec(file_name)
    codec = extract_codec(file_name)
    audio = extract_audio(file_name)
    year = extract_year(file_name)
    source = extract_source(file_name)
    subs = extract_subs(file_name)

    try:
        renamed_base = format_template.format(
            filename=filename_with_ext,
            filename_no_ext=filename_no_ext,
            season=season or "",
            episode=episode or "",
            quality=quality,
            codec=codec,
            vcodec=vcodec,
            acodec=acodec,
            audio=audio,
            year=year,
            source=source,
            subs=subs,
        )
    except KeyError as e:
        logger.warning(f"Unknown placeholder {e} in template for user {user_id}")
        await message.reply_text(
            f"⚠️ **Unknown placeholder** `{e}` in your template.\n\n"
            f"**Available:** `{{season}}` `{{episode}}` `{{quality}}` "
            f"`{{codec}}` `{{vcodec}}` `{{acodec}}` `{{audio}}` `{{year}}` `{{source}}` `{{subs}}` `{{filename}}` `{{filename_no_ext}}`"
        )
        renamed_base = os.path.splitext(safe_original_filename)[0]
    except Exception as e:
        logger.warning(f"Invalid rename template for user {user_id}: {e}")
        renamed_base = os.path.splitext(safe_original_filename)[0]

    # Determine file extension
    video_ext = await codeflixbots.get_video_extension(user_id) or "mkv"
    original_ext = os.path.splitext(safe_original_filename)[1]

    if os.path.splitext(renamed_base)[1]:
        safe_filename = sanitize_filename(renamed_base)
    else:
        # Use custom video extension for video files, original for others
        if message.video or (message.document and original_ext.lower() in [".mkv", ".mp4", ".avi", ".webm", ".mov"]):
            final_ext = f".{video_ext}"
        else:
            final_ext = original_ext
        safe_filename = sanitize_filename(renamed_base + final_ext)

    caption_format = await codeflixbots.get_caption_format(user_id)
    caption_style = await codeflixbots.get_caption_style(user_id)
    
    if caption_format == "as_original":
        caption_text = message.caption or safe_filename
    else:
        caption_text = safe_filename

    caption = format_caption(caption_text, caption_style)
    parse_mode = enums.ParseMode.HTML

    upload_preference = await codeflixbots.get_media_preference(user_id)

    os.makedirs("downloads", exist_ok=True)

    download_path = f"downloads/{time.time()}_{safe_filename}"

    # Track current task info for /queue display
    current_task_info.update({"filename": safe_filename, "stage": "⏳ Queued", "user_id": user_id})

    # Unique token per task
    if task_token is None:
        task_token = f"cancel_{int(time.time() * 1000)}_{user_id}"
    task_owner_map[task_token] = user_id
    cancel_tasks[task_token] = False

    current_task_info["stage"] = "📥 Downloading"
    msg = await message.reply_text(
        "<b>Download</b>\n○○○○○○○○○○ 0%\n<b>Speed:</b> -\n<b>Estimated:</b> -",
        parse_mode="html"
    )

    start = time.time()

    try:
        file_path = await client.download_media(
            message,
            file_name=download_path,
            progress=progress_for_pyrogram,
            progress_args=("📥 Downloading", progress_msg, start, task_token),
        )
    except FloodWait as e:
        logger.warning(f"FloodWait {e.value}s on download for user {user_id}")
        await asyncio.sleep(e.value)
        file_path = await client.download_media(
            message,
            file_name=download_path,
            progress=progress_for_pyrogram,
            progress_args=("📥 Downloading", progress_msg, start, task_token),
        )

    # Cancel check after download
    if cancel_tasks.get(task_token):
        _task_cleanup(task_token)
        await cleanup_files(file_path)
        await safe_edit(progress_msg, "❌ **Task Cancelled.**")
        return
    # ---------------- AUDIO REORDER ----------------
    reorder_task_id = int(task_token.split('_')[1])  # extract timestamp as unique ID
    streams, order = await probe_and_reorder_audio(
        client, file_path, user_id, reorder_task_id, msg, timeout=300
    )
    if order is None:  # User cancelled
        _task_cleanup(task_token)
        await cleanup_files(file_path)
        await safe_edit(msg, "❌ **Task Cancelled.**")
        return

    audio_args = build_audio_map_args(streams, order) if streams else ["-map", "0:a?"]

    try:
        current_task_info["stage"] = "⚙️ Applying Metadata"
        await safe_edit(progress_msg, "⚙️ **Applying Metadata...**")

        metadata_args = await build_metadata_args(user_id, original_title=get_original_title(file_path))

        if metadata_args:
            meta_file = f"downloads/meta_{safe_filename}"
            cmd = [
                "ffmpeg", "-i", file_path,
                "-map", "0:v",
            ] + audio_args + [
                "-map", "0:s?",
                "-c", "copy",
            ] + metadata_args + [
                "-y", meta_file,
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.info(f"Metadata skipped (ffmpeg rc={process.returncode}), using original file")
                await cleanup_files(meta_file)
            else:
                if cancel_tasks.get(task_token):
                    _task_cleanup(task_token)
                    await cleanup_files(file_path, meta_file)
                    await safe_edit(progress_msg, "❌ **Task Cancelled.**")
                    return
                file_path = meta_file
                logger.info(f"Metadata applied for user {user_id}")

    except Exception as e:
        logger.info(f"Metadata step skipped: {e}")

    # ------------ WATERMARK (requires re-encode) ------------
    try:
        wm_apply = await codeflixbots.get_watermark_apply(user_id)
        if wm_apply == "on" and (message.video or (message.document and file_path.lower().endswith(('.mkv', '.mp4', '.avi', '.webm', '.mov')))):
            wm_text = await codeflixbots.get_watermark_text(user_id)
            if wm_text:
                wm_pos = await codeflixbots.get_watermark_position(user_id) or "top_right"
                wm_size = await codeflixbots.get_watermark_size(user_id) or "medium"
                wm_opacity = await codeflixbots.get_watermark_opacity(user_id) or 0.7
                wm_color = await codeflixbots.get_watermark_color(user_id) or "white"
                wm_style = await codeflixbots.get_watermark_style(user_id) or "shadow"
                wm_filter = build_watermark_filter(wm_text, wm_pos, wm_size, wm_opacity, wm_color, wm_style)

                current_task_info["stage"] = "💧 Watermark"
                await safe_edit(progress_msg, "💧 **Applying watermark...** (re-encoding)")

                wm_output = f"downloads/wm_{safe_filename}"
                import subprocess as _sp
                wm_cmd = [
                    "ffmpeg", "-i", file_path,
                    "-vf", wm_filter,
                    "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                    "-c:a", "copy", "-c:s", "copy",
                    "-y", wm_output
                ]
                try:
                    wm_proc = await asyncio.create_subprocess_exec(
                        *wm_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await wm_proc.communicate()
                    if os.path.exists(wm_output) and os.path.getsize(wm_output) > 0:
                        await cleanup_files(file_path)
                        file_path = wm_output
                        logger.info(f"Watermark applied for user {user_id}")
                    else:
                        logger.info(f"Watermark output empty, using original")
                except Exception as wm_err:
                    logger.info(f"Watermark failed ({wm_err}), using original")
    except Exception as e:
        logger.info(f"Watermark skipped: {e}")

    # Get duration for video/audio
    duration = None
    if upload_preference in {"original", "video"} or upload_preference == "audio":
        try:
            import subprocess
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", file_path],
                capture_output=True, text=True, timeout=30)
            duration = int(float(result.stdout.strip()))
        except Exception:
            duration = None

    thumb = None
    thumb_id = await codeflixbots.get_thumbnail(user_id)
    if thumb_id:
        try:
            thumb = await client.download_media(
                thumb_id,
                file_name=f"thumb_{user_id}.jpg"
            )
        except Exception:
            thumb = None

    # Generate thumbnail if none set and it's a video
    if not thumb and (message.video or (message.document and file_path.lower().endswith(('.mkv', '.mp4', '.avi', '.webm', '.mov')))):
        thumb = generate_thumb_at_midpoint(file_path, f"thumb_gen_{user_id}.jpg", duration)
        if thumb:
            logger.info(f"Generated thumbnail for user {user_id}")

    current_task_info["stage"] = "🚀 Uploading"
    await safe_edit(
        msg,
        "<b>Upload</b>\n○○○○○○○○○○ 0%\n<b>Speed:</b> -\n<b>Estimated:</b> -",
        parse_mode="html"
    )

    start = time.time()
    max_retries = 5
    retry_count = 0

    while retry_count < max_retries:

        if cancel_tasks.get(task_token):
            _task_cleanup(task_token)
            await cleanup_files(file_path, thumb)
            await safe_edit(progress_msg, "❌ **Task Cancelled.**")
            return

        try:
            if upload_preference in {"original", "video"} and message.video:
                await client.send_video(
                    chat_id=chat_id,
                    video=file_path,
                    caption=caption,
                    thumb=thumb,
                    duration=duration,
                    progress=progress_for_pyrogram,
                    progress_args=("🚀 Uploading", progress_msg, start, task_token),
                    parse_mode=parse_mode,
                )
            elif upload_preference in {"original", "audio"} and message.audio:
                await client.send_audio(
                    chat_id=chat_id,
                    audio=file_path,
                    caption=caption,
                    thumb=thumb,
                    duration=duration,
                    progress=progress_for_pyrogram,
                    progress_args=("🚀 Uploading", progress_msg, start, task_token),
                    parse_mode=parse_mode,
                )
            elif upload_preference == "music":
                await client.send_audio(
                    chat_id=chat_id,
                    audio=file_path,
                    caption=caption,
                    thumb=thumb,
                    duration=duration,
                    progress=progress_for_pyrogram,
                    progress_args=("🚀 Uploading", progress_msg, start, task_token),
                    parse_mode=parse_mode,
                )
            else:
                await client.send_document(
                    chat_id=chat_id,
                    document=file_path,
                    file_name=safe_filename,
                    caption=caption,
                    thumb=thumb,
                    progress=progress_for_pyrogram,
                    progress_args=("🚀 Uploading", progress_msg, start, task_token),
                    parse_mode=parse_mode,
                )

            break  # Success

        except FloodWait as e:
            retry_count += 1
            wait_time = e.value
            logger.warning(f"FloodWait {wait_time}s on upload for user {user_id} (attempt {retry_count})")
            await safe_edit(progress_msg, f"⏳ **Rate limited** — resuming in `{wait_time}s`...")
            await asyncio.sleep(wait_time)  # Actually sleep the full duration

        except Exception as e:
            retry_count += 1
            logger.error(f"Upload error for user {user_id} (attempt {retry_count}): {e}")
            if retry_count >= max_retries:
                await safe_edit(progress_msg, f"❌ **Upload failed after {max_retries} attempts.**\n`{str(e)[:200]}`")
                break
            await asyncio.sleep(5)

    # Delete original message after processing
    try:
        await message.delete()
    except Exception:
        pass

    await cleanup_files(file_path, thumb)
    await codeflixbots.increment_task_count(user_id, "rename")
    _task_cleanup(task_token)
    current_task_info.clear()

    try:
        await msg.delete()
    except Exception:
        pass


def _task_cleanup(task_token):
    """Clean up task tracking data."""
    task_owner_map.pop(task_token, None)
    cancel_tasks.pop(task_token, None)
