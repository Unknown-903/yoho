import os
import re
import time
import json
import asyncio
import logging
import subprocess
import multiprocessing
from collections import deque

from bot.modules.media_tools.client_compat import Client
from pyrogram import filters, ContinuePropagation, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from bot.helper.media_helper.utils import progress_for_pyrogram, apply_replacor, build_metadata_args
from bot.helper.media_helper.auth import auth_chats
from bot.helper.media_helper.database import codeflixbots
from lang_map import get_language_label, get_original_title
from bot.helper.media_helper.permissions import is_owner, is_admin as _perm_is_admin, is_authorized_chat, can_access_premium_feature
from bot.helper.media_helper.progress import (
    ProgressTracker, SafeProgressEditor, FFmpegProgressMonitor,
    format_download_progress, format_upload_progress, format_bytes, format_time
)
from bot.core.config_manager import Config
from bot.helper.media_helper.command_lock import acquire_lock, release_lock, is_locked
from bot.helper.media_helper.task_manager import task_manager
from bot.helper.media_helper.cleanup import cleanup_task, safe_delete_files

import sys

class _ProbeStopSignal(Exception):
    """Sentinel to stop 5MB probe download. Not an error."""
    pass

class _ProbeLogFilter(logging.Filter):
    """Suppress _ProbeStopSignal from Pyrogram internal logs."""
    def filter(self, record):
        # Check the formatted exception info (where Pyrogram logs the traceback)
        if record.exc_info and record.exc_info[0] is not None:
            exc_type = record.exc_info[0]
            if exc_type.__name__ == "_ProbeStopSignal":
                return False
        # Also check message text as fallback
        try:
            msg = record.getMessage()
            if "_ProbeStopSignal" in msg or "__probe_done__" in msg:
                return False
        except: pass
        return True

for _ln in ["pyrogram.client", "pyrogram", "pyrogram.session.session"]:
    logging.getLogger(_ln).addFilter(_ProbeLogFilter())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ================= ADMIN CHECK =================

def _is_admin_encode(user_id):
    return _perm_is_admin(user_id)

# ================= CONSTANTS =================

CODECS = {
    "h265": {"lib": "libx265", "tag": "hvc1", "label": "🎬 H.265 (HEVC)"},
    "h264": {"lib": "libx264", "tag": "avc1", "label": "📺 H.264 (AVC)"},
}

RESOLUTIONS = {
    "original": None, "360p": "640:360", "480p": "854:480", "540p": "960:540",
    "720p": "1280:720", "1080p": "1920:1080", "4k": "3840:2160",
}

RESOLUTION_WIDTHS = {
    "360p": 640, "480p": 854, "540p": 960, "720p": 1280, "1080p": 1920, "4k": 3840,
}

DEFAULT_CRF = {
    "360p": 30, "480p": 28, "540p": 27, "720p": 26,
    "1080p": 24, "4k": 22, "original": 24,
}
MIN_BITRATE = {
    "360p": 200, "480p": 350, "540p": 500, "720p": 700,
    "1080p": 1400, "4k": 3000, "original": 700,
}
MAX_BITRATE = {
    "360p": 800, "480p": 1200, "540p": 1800, "720p": 2500,
    "1080p": 4500, "4k": 9000, "original": 4500,
}
SIZE_PER_MIN = {
    "360p": 1.5, "480p": 2.5, "540p": 3.5, "720p": 5.0,
    "1080p": 8.5, "4k": 20.0, "original": 5.0,
}

PRESETS = [
    "ultrafast", "superfast", "veryfast", "faster",
    "fast", "medium", "slow", "slower", "veryslow",
]

AUDIO_CODECS = {
    "aac": {"lib": "aac", "label": "🔊 AAC"},
    "ac3": {"lib": "ac3", "label": "🔊 AC3"},
    "opus": {"lib": "libopus", "label": "🔊 OPUS"},
    "mp3": {"lib": "libmp3lame", "label": "🔊 MP3"},
    "copy": {"lib": "copy", "label": "📋 Copy Original"},
}

AUDIO_CHANNELS = {
    "stereo": {"val": "2"}, "mono": {"val": "1"},
    "5.1": {"val": "6"}, "original": {"val": None},
}

COMPRESS_LEVELS = {
    "low": {"ratio": 0.85}, "medium": {"ratio": 0.65},
    "high": {"ratio": 0.45}, "best": {"ratio": 0.30}, "skip": {"ratio": 1.0},
}

PATIENCE_MSGS = [
    "☕ Chai pi lo, thoda time lagega...",
    "🍿 Popcorn ready karo, abhi aa raha hai!",
    "🐢 HEVC encoding slow hoti hai, quality ke liye worth it hai!",
    "🔧 FFmpeg mehnat kar raha hai aapke liye...",
    "⚡ Server full speed pe hai, bas thoda sabr karo...",
    "🧘 Patience is a virtue... aur encoding bhi!",
    "🚀 Quality encode ho rahi hai, rush mat karo!",
]

# ================= HELPERS =================

def get_video_duration(file_path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except:
        return None

def get_video_width(file_path):
    """Get video width from file using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True, timeout=30)
        return int(result.stdout.strip())
    except:
        return 1280

def has_video_stream(file_path):
    """Check if file has a video stream."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=codec_type",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True, timeout=30)
        return bool(result.stdout.strip())
    except:
        return True  # assume video if probe fails

def get_audio_streams_info(file_path):
    """Get audio streams with language/title for reorder UI."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index,codec_name,channels:stream_tags=language,title",
             "-of", "json", file_path],
            capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except:
        return []

def calc_video_bitrate(duration_sec, quality, audio_bitrate_kbps=128):
    minutes = duration_sec / 60
    target_mb = SIZE_PER_MIN.get(quality, 5.0) * minutes
    target_bits = target_mb * 8 * 1024 * 1024
    total_kbps = (target_bits / duration_sec) / 1000
    video_kbps = int(total_kbps - audio_bitrate_kbps)
    video_kbps = max(video_kbps, MIN_BITRATE.get(quality, 350))
    video_kbps = min(video_kbps, MAX_BITRATE.get(quality, 2500))
    return video_kbps

def calc_max_bitrate(duration_sec, quality, audio_bitrate_kbps=128):
    target = calc_video_bitrate(duration_sec, quality, audio_bitrate_kbps)
    return min(int(target * 1.4), int(MAX_BITRATE.get(quality, 2500) * 1.2))

def _get_size_ratio(wm_size):
    """Convert size setting to a ratio (0.0-1.0)."""
    preset_ratios = {"small": 0.08, "medium": 0.15, "large": 0.25}
    if wm_size in preset_ratios: return preset_ratios[wm_size]
    if wm_size.endswith("%"):
        try: return int(wm_size.rstrip("%")) / 100
        except ValueError: pass
    return 0.15

def build_watermark_filter(wm_text, wm_position, wm_size, wm_opacity, wm_color="white", wm_style="shadow"):
    """Build FFmpeg drawtext filter with color & style support.
    
    Styles:
      shadow  — text with drop shadow (clean, professional)
      outline — thick border outline (high visibility)
      glow    — bright text with soft glow effect
      neon    — vibrant colored border + shadow
      clean   — plain text, no effects
      bold    — thick shadow + border (maximum readability)
    """
    preset_sizes = {"small": "18", "medium": "28", "large": "42", "xlarge": "56"}
    if wm_size in preset_sizes:
        fontsize_expr = preset_sizes[wm_size]
    elif wm_size.endswith("%"):
        try:
            pct = int(wm_size.rstrip("%")) / 100
            fontsize_expr = f"h*{pct}"
        except ValueError:
            fontsize_expr = "28"
    else:
        fontsize_expr = "28"
    
    positions = {
        "top_left": "x=20:y=20", "top_right": "x=w-tw-20:y=20",
        "bottom_left": "x=20:y=h-th-20", "bottom_right": "x=w-tw-20:y=h-th-20",
        "center": "x=(w-tw)/2:y=(h-th)/2",
    }
    pos = positions.get(wm_position, "x=w-tw-20:y=20")
    safe_text = wm_text.replace("'", "\\'").replace(":", "\\:")
    alpha = max(0.1, min(1.0, wm_opacity))
    
    # Color handling — supports named colors + hex
    color = wm_color if wm_color else "white"
    
    # Style presets
    if wm_style == "shadow":
        effects = f"shadowcolor=black@{alpha*0.8}:shadowx=2:shadowy=2:borderw=1:bordercolor=black@{alpha*0.5}"
    elif wm_style == "outline":
        effects = f"borderw=3:bordercolor=black@{alpha}"
    elif wm_style == "glow":
        effects = f"shadowcolor={color}@{alpha*0.4}:shadowx=0:shadowy=0:borderw=2:bordercolor=white@{alpha*0.6}"
    elif wm_style == "neon":
        effects = f"borderw=2:bordercolor={color}@{alpha}:shadowcolor={color}@{alpha*0.5}:shadowx=3:shadowy=3"
    elif wm_style == "bold":
        effects = f"borderw=4:bordercolor=black@{alpha}:shadowcolor=black@{alpha}:shadowx=3:shadowy=3"
    else:  # clean
        effects = ""
    
    base = f"drawtext=text=\'{safe_text}\':fontsize={fontsize_expr}:{pos}:fontcolor={color}@{alpha}"
    if effects:
        base += f":{effects}"
    
    return base

def get_overlay_position(wm_position):
    """Get FFmpeg overlay position string (uses W,H for main, w,h for overlay)."""
    positions = {
        "top_left": "x=10:y=10", "top_right": "x=W-w-10:y=10",
        "bottom_left": "x=10:y=H-h-10", "bottom_right": "x=W-w-10:y=H-h-10",
        "center": "x=(W-w)/2:y=(H-h)/2",
    }
    return positions.get(wm_position, "x=W-w-10:y=10")

def _build_audio_order_text(streams, order):
    """Build numbered text list for audio streams."""
    text = "🎛 **Audio Track Order**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, idx in enumerate(order):
        s = streams[idx]
        tags = s.get("tags", {})
        lang = get_language_label(tags.get("language", "und"))
        title = tags.get("title", "")
        codec = s.get("codec_name", "?").upper()
        ch = s.get("channels", "?")
        label = f"{title} ({lang})" if title else lang
        prefix = "🔊 **DEFAULT** →" if i == 0 else f"  {i+1}."
        text += f"{prefix} {label} | {codec} | {ch}ch\n"
    text += "\n━━━━━━━━━━━━━━━━━━━━\n"
    text += "💡 Tap track → moves to #1 (default)\n"
    return text

def _build_audio_order_buttons(streams, order, task_id, user_id):
    buttons = []
    for i, idx in enumerate(order):
        s = streams[idx]
        tags = s.get("tags", {})
        lang = get_language_label(tags.get("language", "und"))
        title = tags.get("title", "")
        codec = s.get("codec_name", "?").upper()
        ch = s.get("channels", "?")
        ch_label = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(ch, f"{ch}ch") if isinstance(ch, int) else f"{ch}ch"
        name_part = f"{title} ({lang})" if title else lang
        detail_part = f"{codec} {ch_label}"
        prefix = "🔊 " if i == 0 else ""
        label = f"{prefix}{name_part} | {detail_part}"
        if len(label) > 60:
            label = label[:57] + "..."
        buttons.append([InlineKeyboardButton(label, callback_data=f"enc_aorder|{task_id}|{idx}")])
    buttons.append([
        InlineKeyboardButton("✅ Continue Encoding", callback_data=f"enc_aorder_done|{task_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"cancel|{task_id}|{user_id}"),
    ])
    return InlineKeyboardMarkup(buttons)

# ================= QUEUE =================

encode_queue = asyncio.Queue()
queue_list = deque()
active_tasks = {}
workers_started = False
encode_state = {}
cancel_tasks = {}
worker_tasks = set()  # Store worker task references for cleanup
shutdown_event = None  # Set during bot shutdown

# Audio reorder events: task_id -> asyncio.Event
audio_order_events = {}
# Audio reorder data: task_id -> list (order)
audio_order_data = {}

# ================= WORKER =================

async def start_workers(client):
    global workers_started, shutdown_event
    if workers_started:
        return
    workers_started = True
    shutdown_event = asyncio.Event()
    task = asyncio.create_task(worker(client))
    worker_tasks.add(task)
    task.add_done_callback(worker_tasks.discard)


async def worker(client):
    while not shutdown_event.is_set():
        try:
            # Wait for task with timeout to allow shutdown event check
            task = await asyncio.wait_for(encode_queue.get(), timeout=1.0)
            active_tasks[task["id"]] = task
            _uid = task.get("user", 0)
            _fname = ""
            try:
                _m = task.get("msg")
                if _m:
                    _fname = getattr(getattr(_m, "document", None), "file_name", "") or getattr(getattr(_m, "video", None), "file_name", "") or ""
            except: pass
            acquire_lock(_uid, "encode", task_id=task["id"], file_name=_fname)
            task_manager.register(task["id"], _uid, "encode", file_name=_fname, username=str(task.get("username","")))
            try:
                await start_encode(client, task)
            except asyncio.CancelledError:
                logger.warning(f"[{task['id']}] Encode task cancelled")
                task_manager.set_error(task["id"], "Cancelled")
                try:
                    _m = task.get("msg")
                    if _m: await _m.reply_text("❌ **Encode cancelled and cleaned up.**")
                except: pass
                raise
            except Exception as e:
                logger.error(f"Encode error: {e}", exc_info=True)
                task_manager.set_error(task["id"], str(e)[:100])
                # Immediately notify user of error
                try:
                    _m = task.get("msg")
                    if _m: await _m.reply_text(f"❌ **Encode failed!**\n`{str(e)[:200]}`")
                except: pass
            finally:
                release_lock(_uid, "encode")
                task_manager.complete(task["id"])
                active_tasks.pop(task["id"], None)
                cancel_tasks.pop(task["id"], None)
                audio_order_events.pop(task["id"], None)
                audio_order_data.pop(task["id"], None)
                try:
                    queue_list.remove(task)
                except:
                    pass
                encode_queue.task_done()
        except asyncio.TimeoutError:
            # Timeout is ok, check shutdown_event again
            continue
        except asyncio.CancelledError:
            logger.info("Worker shutdown requested")
            break


async def encode_cleanup():
    """Call this during bot shutdown to cleanup encode tasks"""
    global shutdown_event
    if shutdown_event:
        shutdown_event.set()
    
    # Cancel all worker tasks
    for task in list(worker_tasks):
        if not task.done():
            task.cancel()
    
    # Wait for all tasks to complete
    if worker_tasks:
        await asyncio.gather(*worker_tasks, return_exceptions=True)


def get_media_duration(file_path):
    """Get duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except:
        return None

def generate_thumb_at_midpoint(file_path, output_thumb, duration=None):
    """Generate a thumbnail at 50% of the video duration using ffmpeg."""
    try:
        if duration is None:
            duration = get_media_duration(file_path)
        if not duration or duration <= 0:
            return None
        midpoint = duration / 2
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(midpoint), "-i", file_path,
             "-vframes", "1", "-q:v", "2", "-vf", "scale=320:-2", output_thumb],
            capture_output=True, timeout=30)
        if os.path.exists(output_thumb) and os.path.getsize(output_thumb) > 0:
            return output_thumb
        return None
    except:
        return None

# ================= AUDIO DETECTION =================

AUDIO_EXTENSIONS = {".mp3", ".aac", ".m4a", ".opus", ".flac", ".ogg", ".wav", ".wma", ".alac"}

def _is_audio_only_msg(msg):
    """Check if a message contains only audio (no video)."""
    if msg.audio:
        return True
    if msg.video:
        return False
    if msg.document:
        name = (msg.document.file_name or "").lower()
        return any(name.endswith(ext) for ext in AUDIO_EXTENSIONS)
    return False

# ================= BATCH ENCODE STATE =================

batch_encode_state = {}  # user_id -> {"files": [messages], "settings": {...}}
batch_af_decision = {}  # user_id -> audio_order (list) or "skip" — AF decision for entire batch

@Client.on_message((filters.private | filters.group) & filters.command("encode") & ~filters.reply)
async def batch_encode_cmd(client, message):
    user_id = message.from_user.id
    if not _is_admin_encode(user_id):
        return await message.reply_text("❌ Only owner/admin can use this.")
    if message.chat.type in ["group", "supergroup"]:
        if not is_authorized_chat(message.chat.id):
            return await message.reply_text("❌ This group is not authorized.")
    
    # === COMMAND LOCK CHECK ===
    if is_locked(user_id, "encode"):
        return await message.reply_text("⚠️ **Encode Already Running**\n\nYou have an active /encode. Wait or cancel.\n💡 /compress, /merge, /rename still work!")
    # Initialize batch state
    batch_encode_state[user_id] = {"files": [], "settings": None, "waiting": True}
    await start_workers(client)
    
    await message.reply_text(
        "📥 **Batch Encode Mode**\n\n"
        "Send me video, document, or audio files.\n"
        "Send `/edone` when ready.\n\n"
        "ℹ️ Audio-only batches skip video settings automatically."
    )

@Client.on_message((filters.private | filters.group) & filters.command("edone"))
async def process_batch_encode(client, message):
    user_id = message.from_user.id
    logger.info(f"[{user_id}] /edone command received")
    if not _is_admin_encode(user_id):
        logger.info(f"[{user_id}] Not admin, sending message")
        await message.reply_text("❌ Only admins can use batch processing.")
        return
    if message.chat.type in ["group", "supergroup"]:
        if not is_authorized_chat(message.chat.id):
            logger.info(f"[{user_id}] Group not authorized, sending message")
            await message.reply_text("❌ This group is not authorized for batch processing.")
            return
    
    if user_id not in batch_encode_state:
        logger.info(f"[{user_id}] No batch state for encode, sending message")
        await message.reply_text("❌ No batch encode session active. Send /encode first.")
        return  # Silently return if not in encode batch mode
    
    state = batch_encode_state[user_id]
    files = state.get("files", [])
    logger.info(f"[{user_id}] Found {len(files)} files in batch")
    if not files:
        logger.info(f"[{user_id}] No files, sending error")
        await message.reply_text("❌ No files collected. Send some video files first.")
        return
    
    # Detect if all files are audio-only
    all_audio = all(_is_audio_only_msg(f) for f in files)
    
    # Start settings selection
    encode_state[user_id] = {
        "batch_files": files, "step": "codec", "batch_mode": True,
        "audio_only": all_audio
    }
    
    if all_audio:
        # Audio-only batch: skip video codec + resolution → audio options
        encode_state[user_id]["codec"] = "h265"
        encode_state[user_id]["quality"] = "original"
        await message.reply_text(
            f"🎵 **{len(files)} audio file(s) detected**\n"
            "Skipping video settings → audio options"
        )
        batch_encode_state.pop(user_id, None)
        batch_af_decision.pop(user_id, None)
        return await _ask_audio_codec(client, message, user_id)
    
    saved_codec = await codeflixbots.get_encode_codec(user_id)
    if saved_codec != "ask":
        encode_state[user_id]["codec"] = saved_codec
        batch_encode_state.pop(user_id, None)
        return await _ask_resolution(client, message, user_id)
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 H.265 (HEVC)", callback_data="enc_codec|h265"),
         InlineKeyboardButton("📺 H.264 (AVC)", callback_data="enc_codec|h264")]
    ])
    await message.reply_text(
            f"🎬 **Video Codec** — {len(files)} files\n\n"
            "H.265 → Smaller size, better quality\n"
            "H.264 → Wider compatibility",
            reply_markup=buttons)
    batch_encode_state.pop(user_id, None)  # Clean up batch state

@Client.on_message(
    (filters.private | filters.group) & (filters.video | filters.document | filters.audio),
    group=2
)
async def batch_encode_file_handler(client, message):
    user_id = message.from_user.id if message.from_user else None
    if not user_id or user_id not in batch_encode_state or not batch_encode_state[user_id].get("waiting"):
        raise ContinuePropagation
    
    # Silently add file to batch (no reply to avoid FloodWait)
    state = batch_encode_state[user_id]
    state["files"].append(message)

# ================= ENCODE COMMAND =================

@Client.on_message((filters.private | filters.group) & filters.command("encode") & filters.reply)
async def encode_cmd(client, message):
    user_id = message.from_user.id
    
    # Check premium access (admin or premium user)
    if not await can_access_premium_feature(user_id):
        contact = Config.ADMIN_URL or "the bot owner"
        return await message.reply_text(
            "❌ **Premium Feature**\n\n"
            "Encoding is available for:\n"
            "✅ Admin/Owner\n"
            "✅ Premium Members\n\n"
            f"📞 Contact {contact} for premium access!"
        )
    
    if message.chat.type in ["group", "supergroup"]:
        if not is_authorized_chat(message.chat.id):
            return await message.reply_text("❌ This group is not authorized.")
    if not message.reply_to_message:
        return await message.reply_text("❌ Reply to a video or file")
    if not (message.reply_to_message.video or message.reply_to_message.document):
        return await message.reply_text("❌ Reply to a downloadable media file")

    encode_state[user_id] = {"msg": message.reply_to_message, "step": "codec"}
    await start_workers(client)

    saved_codec = await codeflixbots.get_encode_codec(user_id)
    if saved_codec != "ask":
        encode_state[user_id]["codec"] = saved_codec
        return await _ask_resolution(client, message, user_id)

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 H.265 (HEVC)", callback_data="enc_codec|h265"),
         InlineKeyboardButton("📺 H.264 (AVC)", callback_data="enc_codec|h264")]
    ])
    await message.reply_text(
            "🎬 **Video Codec**\n\n"
            "H.265 → Smaller size, better quality\n"
            "H.264 → Wider compatibility",
            reply_markup=buttons)

async def _ask_resolution(client, msg_or_q, uid):
    saved = await codeflixbots.get_encode_resolution(uid)
    if saved != "ask":
        encode_state[uid]["quality"] = saved
        return await _ask_preset(client, msg_or_q, uid)
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 360p", callback_data="enc_res|360p"),
         InlineKeyboardButton("🎬 480p", callback_data="enc_res|480p"),
         InlineKeyboardButton("📺 540p", callback_data="enc_res|540p")],
        [InlineKeyboardButton("🖥️ 720p", callback_data="enc_res|720p"),
         InlineKeyboardButton("🔥 1080p", callback_data="enc_res|1080p"),
         InlineKeyboardButton("💎 4K", callback_data="enc_res|4k")],
        [InlineKeyboardButton("🎯 Original", callback_data="enc_res|original")],
    ])
    text = ("📐 **Resolution**\n\n"
            "Higher = sharper but larger file\n"
            "Original = keep source resolution")
    if hasattr(msg_or_q, 'edit_text'): await msg_or_q.edit_text(text, reply_markup=buttons)
    else: await msg_or_q.reply_text(text, reply_markup=buttons)

async def _ask_preset(client, msg_or_q, uid):
    saved = await codeflixbots.get_encode_preset(uid)
    if saved != "ask":
        encode_state[uid]["preset"] = saved
        return await _ask_compress(client, msg_or_q, uid)
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ ultrafast", callback_data="enc_pre|ultrafast"),
         InlineKeyboardButton("🚀 superfast", callback_data="enc_pre|superfast")],
        [InlineKeyboardButton("🔥 veryfast", callback_data="enc_pre|veryfast"),
         InlineKeyboardButton("💨 faster", callback_data="enc_pre|faster")],
        [InlineKeyboardButton("⚙️ fast", callback_data="enc_pre|fast"),
         InlineKeyboardButton("🎯 medium", callback_data="enc_pre|medium")],
        [InlineKeyboardButton("🐢 slow", callback_data="enc_pre|slow"),
         InlineKeyboardButton("🐌 slower", callback_data="enc_pre|slower")],
        [InlineKeyboardButton("🧊 veryslow", callback_data="enc_pre|veryslow")],
    ])
    text = ("⚡ **Encoding Speed**\n\n"
            "Faster = quicker but slightly larger file\n"
            "Slower = takes longer, better compression")
    if hasattr(msg_or_q, 'edit_text'): await msg_or_q.edit_text(text, reply_markup=buttons)
    else: await msg_or_q.reply_text(text, reply_markup=buttons)

async def _ask_compress(client, msg_or_q, uid):
    saved = await codeflixbots.get_encode_compress(uid)
    if saved != "ask":
        encode_state[uid]["compress_level"] = saved
        return await _ask_audio_codec(client, msg_or_q, uid)
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Low", callback_data="enc_cmp|low"),
         InlineKeyboardButton("🟡 Medium", callback_data="enc_cmp|medium")],
        [InlineKeyboardButton("🟠 High", callback_data="enc_cmp|high"),
         InlineKeyboardButton("🔴 Best", callback_data="enc_cmp|best")],
        [InlineKeyboardButton("⏭️ Skip", callback_data="enc_cmp|skip")],
    ])
    text = ("🗜️ **Compression Level**\n\n"
            "Higher = smaller file size\n"
            "Skip = no extra compression")
    if hasattr(msg_or_q, 'edit_text'): await msg_or_q.edit_text(text, reply_markup=buttons)
    else: await msg_or_q.reply_text(text, reply_markup=buttons)

async def _ask_audio_codec(client, msg_or_q, uid):
    saved = await codeflixbots.get_encode_audio_codec(uid)
    if saved != "ask":
        encode_state[uid]["audio_codec"] = saved
        return await _ask_watermark(client, msg_or_q, uid)
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔊 AAC", callback_data="enc_acodec|aac"),
         InlineKeyboardButton("🔊 AC3", callback_data="enc_acodec|ac3")],
        [InlineKeyboardButton("🔊 OPUS", callback_data="enc_acodec|opus"),
         InlineKeyboardButton("🔊 MP3", callback_data="enc_acodec|mp3")],
        [InlineKeyboardButton("📋 Copy Original", callback_data="enc_acodec|copy")],
    ])
    text = ("🔊 **Audio Codec**\n\n"
            "AAC/OPUS = good quality, small size\n"
            "Copy = keep original audio untouched")
    if hasattr(msg_or_q, 'edit_text'): await msg_or_q.edit_text(text, reply_markup=buttons)
    else: await msg_or_q.reply_text(text, reply_markup=buttons)


async def _ask_watermark(client, msg_or_q, uid):
    """Check watermark_apply mode: on/off/ask."""
    wm_apply = await codeflixbots.get_watermark_apply(uid)
    
    if wm_apply == "on":
        encode_state[uid]["apply_watermark"] = True
        return await _ask_rename(client, msg_or_q, uid)
    elif wm_apply == "off":
        encode_state[uid]["apply_watermark"] = False
        return await _ask_rename(client, msg_or_q, uid)
    else:
        # "ask" — show inline
        # Check if watermark is even configured
        wm_text = await codeflixbots.get_watermark_text(uid)
        wm_image = await codeflixbots.get_watermark_image(uid)
        if not wm_text and not wm_image:
            # No watermark configured, skip
            encode_state[uid]["apply_watermark"] = False
            return await _ask_rename(client, msg_or_q, uid)
        
        preview = f"`{wm_text}`" if wm_text else "Image watermark"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Apply Watermark", callback_data="enc_wm|yes"),
             InlineKeyboardButton("❌ Skip", callback_data="enc_wm|no")]
        ])
        text = (f"💧 **Watermark**\n\n"
                f"Current: {preview}\n"
                f"Apply to this encode?")
        if hasattr(msg_or_q, 'edit_text'): await msg_or_q.edit_text(text, reply_markup=buttons)
        else: await msg_or_q.reply_text(text, reply_markup=buttons)

async def _ask_rename(client, msg_or_q, uid):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Same as Caption", callback_data="enc_rename|caption")],
        [InlineKeyboardButton("📄 Keep Filename", callback_data="enc_rename|no"),
         InlineKeyboardButton("✏️ Set New", callback_data="enc_rename|yes")]
    ])
    text = ("✏️ **Output Filename**\n\n"
            "Choose how to name the output file")
    if hasattr(msg_or_q, 'edit_text'): await msg_or_q.edit_text(text, reply_markup=buttons)
    else: await msg_or_q.reply_text(text, reply_markup=buttons)

async def _finalize_task(client, uid, rename=None):
    state = encode_state.pop(uid, {})
    if not state:
        return None
    
    batch_mode = state.get("batch_mode", False)
    files = state.get("batch_files", [state.get("msg")]) if batch_mode else [state.get("msg")]
    
    if not files or not files[0]:
        return None
    
    quality = state.get("quality", "720p")
    base_task = {
        "codec": state.get("codec", "h265"), "quality": quality,
        "preset": state.get("preset", "veryfast"),
        "compress_level": state.get("compress_level", "skip"),
        "audio_codec": state.get("audio_codec", "aac"),
        "rename": rename, "crf": DEFAULT_CRF.get(quality, 24),
        "name": state.get("user_name", "User"),
        "apply_watermark": state.get("apply_watermark", True),
    }
    
    tasks_added = []
    for i, msg in enumerate(files):
        task_id = int(time.time() * 1000) + i  # Unique IDs
        task = {
            "id": task_id, "user": uid, "msg": msg,
            **base_task
        }
        queue_list.append(task)
        await encode_queue.put(task)
        tasks_added.append(task_id)
    
    codec_label = CODECS.get(base_task["codec"], {}).get("label", base_task["codec"])
    file_count = len(tasks_added)
    return (f"📥 **Added {file_count} files to Encode Queue**\n\n🎬 {codec_label}\n"
            f"📐 {base_task['quality']} | ⚡ {base_task['preset']}\n"
            f"🗜️ {base_task['compress_level'].title()} | 🔊 {base_task['audio_codec'].upper()}\n"
            f"📍 Position: {len(queue_list) - file_count + 1}-{len(queue_list)}")

# ================= CALLBACKS =================

@Client.on_callback_query(filters.regex("^enc_codec"))
async def enc_codec_cb(client, q):
    uid = q.from_user.id; _, codec = q.data.split("|")
    if uid not in encode_state: return await q.answer("Session expired.", show_alert=True)
    encode_state[uid]["codec"] = codec; encode_state[uid]["user_name"] = q.from_user.first_name
    await _ask_resolution(client, q.message, uid)

@Client.on_callback_query(filters.regex("^enc_res"))
async def enc_res_cb(client, q):
    uid = q.from_user.id; _, val = q.data.split("|")
    if uid not in encode_state: return await q.answer("Session expired.", show_alert=True)
    encode_state[uid]["quality"] = val; encode_state[uid]["user_name"] = q.from_user.first_name
    await _ask_preset(client, q.message, uid)

@Client.on_callback_query(filters.regex("^enc_pre"))
async def enc_preset_cb(client, q):
    uid = q.from_user.id; _, val = q.data.split("|")
    if uid not in encode_state: return await q.answer("Session expired.", show_alert=True)
    encode_state[uid]["preset"] = val
    await _ask_compress(client, q.message, uid)

@Client.on_callback_query(filters.regex("^enc_cmp"))
async def enc_compress_cb(client, q):
    uid = q.from_user.id; _, val = q.data.split("|")
    if uid not in encode_state: return await q.answer("Session expired.", show_alert=True)
    encode_state[uid]["compress_level"] = val
    await _ask_audio_codec(client, q.message, uid)

@Client.on_callback_query(filters.regex("^enc_acodec"))
async def enc_audio_cb(client, q):
    uid = q.from_user.id; _, val = q.data.split("|")
    if uid not in encode_state: return await q.answer("Session expired.", show_alert=True)
    encode_state[uid]["audio_codec"] = val
    await _ask_watermark(client, q.message, uid)


@Client.on_callback_query(filters.regex("^enc_wm"))
async def enc_wm_cb(client, q):
    uid = q.from_user.id; _, choice = q.data.split("|")
    if uid not in encode_state: return await q.answer("Session expired.", show_alert=True)
    encode_state[uid]["apply_watermark"] = (choice == "yes")
    await q.answer("✅ Watermark ON" if choice == "yes" else "❌ Watermark OFF")
    await _ask_rename(client, q.message, uid)

@Client.on_callback_query(filters.regex("^enc_rename"))
async def enc_rename_cb(client, q):
    uid = q.from_user.id; _, choice = q.data.split("|")
    if uid not in encode_state: return await q.answer("Session expired.", show_alert=True)
    if choice == "yes":
        encode_state[uid]["waiting_rename"] = True
        await q.message.edit_text("✏️ **Send new file name**\nExample: `Episode 10`")
    elif choice == "caption":
        text = await _finalize_task(client, uid, rename="__CAPTION__")
        if text: await q.message.edit_text(text)
    else:
        text = await _finalize_task(client, uid, rename=None)
        if text: await q.message.edit_text(text)

# Audio reorder callback (during encoding, after download)
@Client.on_callback_query(filters.regex(r"^enc_aorder\|"))
async def enc_aorder_cb(client, q):
    parts = q.data.split("|")
    task_id = int(parts[1]); stream_idx = int(parts[2])
    order = audio_order_data.get(task_id)
    if not order: return await q.answer("Session expired.", show_alert=True)
    task = active_tasks.get(task_id)
    if not task: return await q.answer("Task not found.", show_alert=True)
    # Move selected to #1
    if stream_idx in order:
        order.remove(stream_idx)
        order.insert(0, stream_idx)
    streams = task.get("_audio_streams", [])
    text = _build_audio_order_text(streams, order)
    buttons = _build_audio_order_buttons(streams, order, task_id, task["user"])
    try:
        await q.message.edit_text(text, reply_markup=buttons)
        await q.answer("🔊 Moved to #1")
    except: pass

@Client.on_callback_query(filters.regex(r"^enc_aorder_done\|"))
async def enc_aorder_done_cb(client, q):
    task_id = int(q.data.split("|")[1])
    event = audio_order_events.get(task_id)
    if event: event.set()
    await q.answer("✅ Continuing encode...")

# ================= RENAME TEXT HANDLER =================

@Client.on_message(
    (filters.private | filters.group) & filters.text &
    ~filters.command(["encode","start","help","settings","queue","cancel",
                      "setthumb","delthumb","viewthumb","setcaption","delcaption",
                      "seecaption","metadata","delmetadata","addadmin","removeadmin",
                      "adminlist","authgroup","unauthgroup","authlist","rename","logs",
                      "batch","cancelbatch","speedtest","status","leaderboard","top","lb",
                      "af","broadcast","add","rm","addlist","clearselect"]),
    group=2)
async def get_encode_rename(client, message):
    uid = message.from_user.id
    if uid not in encode_state: return
    data = encode_state.get(uid)
    if not data or not data.get("waiting_rename"): return
    text = await _finalize_task(client, uid, rename=message.text)
    if text: await message.reply_text(text)

# ================= QUEUE COMMAND =================

@Client.on_callback_query(filters.regex(r"^cancel\|"))
async def cancel_task_encode(client, q):
    parts = q.data.split("|")
    if len(parts) != 3: return await q.answer("Invalid", show_alert=True)
    _, task_id, owner_id = parts; task_id = int(task_id); owner_id = int(owner_id)
    caller = q.from_user.id
    if caller == owner_id or caller == Config.OWNER_ID:
        cancel_tasks[task_id] = True
        # Kill ffmpeg subprocess if running
        _task = active_tasks.get(task_id)
        if _task and "_ffmpeg_process" in _task:
            try:
                _task["_ffmpeg_process"].kill()
                logger.info(f"[{task_id}] FFmpeg process killed")
            except: pass
        # Unblock audio reorder wait if pending
        event = audio_order_events.get(task_id)
        if event: event.set()
        # Clean up temp files immediately
        if _task:
            _uid = _task.get("user", 0)
            for _p in [f"temp_{task_id}.mkv", f"enc_{task_id}.mkv", f"probe_{task_id}.mkv",
                       f"autothumb_{task_id}.jpg"]:
                if os.path.exists(_p):
                    try: os.remove(_p)
                    except: pass
        await q.answer("✅ Cancelled — process killed & files cleaned")
    else:
        await q.answer("❌ Not your task!", show_alert=True)

# ================= ENCODING ENGINE =================

async def start_encode(client, task):
    msg = task["msg"]; user_id = task["user"]; quality = task["quality"]
    preset = task.get("preset", "veryfast"); codec_key = task.get("codec", "h265")
    audio_codec_key = task.get("audio_codec", "aac"); rename = task["rename"]
    crf = task["crf"]; codec_info = CODECS.get(codec_key, CODECS["h265"])
    scale = RESOLUTIONS.get(quality)
    ext = await codeflixbots.get_video_extension(user_id) or "mkv"
    ext = f".{ext.lstrip('.')}"
    download = f"temp_{task['id']}.mkv"; encoded = f"enc_{task['id']}.mkv"
    cancel_tasks[task['id']] = False

    # Clear stale batch AF decision so AF UI shows fresh each session
    batch_af_decision.pop(user_id, None)

        # ---- 5MB SEGMENT DOWNLOAD for audio probe ----
    settings_info = f"{codec_info['label'].split()[-1]} • {quality}"
    _cancel_data = f"cancel|{task['id']}|{user_id}"
    progress_msg = await msg.reply_text(
        f"<b>Probing</b> • {settings_info}\n"
        f"⏳ Downloading 5MB segment...\n"
        f"/cancel {task['id']}",
        parse_mode="html"
    )
    task_manager.update_progress(task["id"], status="probing")

    # Download 5MB segment for fast probe (stops download early)
    _probe_path = f"probe_{task['id']}.mkv"
    _PROBE_LIMIT = 5 * 1024 * 1024  # 5MB

    async def _probe_stop(current, total, *args):
        if current >= _PROBE_LIMIT:
            raise _ProbeStopSignal()

    try:
        await client.download_media(msg, file_name=_probe_path, progress=_probe_stop)
    except _ProbeStopSignal:
        pass  # Expected: 5MB probe limit reached
    except Exception as _e:
        logger.warning(f"Probe segment error: {_e}")

    if cancel_tasks.get(task['id']):
        _cleanup_files([_probe_path]); return

    # Probe audio tracks from 5MB segment
    # Probe result logging
    audio_streams = []
    if os.path.exists(_probe_path):
        audio_streams = get_audio_streams_info(_probe_path)
        logger.info(f"[{task['id']}] Probe found {len(audio_streams)} audio streams")
        _cleanup_files([_probe_path])  # delete segment, will download full later
    else:
        logger.info(f"[{task['id']}] Probe file not found at {_probe_path} — will probe after full download")
    task["_audio_streams"] = audio_streams
    audio_map_order = list(range(len(audio_streams)))

    # Batch AF: if already decided for this batch, reuse
    _batch_af = batch_af_decision.get(user_id)
    logger.info(f"[{task['id']}] batch_af={_batch_af}, audio_streams={len(audio_streams)}")
    if _batch_af == "skip":
        pass  # keep default order
    elif isinstance(_batch_af, list):
        # Reuse previous batch order (smart match by language)
        from helper.audio_reorder import smart_reorder_by_priority
        _priority = []
        for idx in _batch_af:
            if idx < len(task.get("_prev_streams", [])):
                t = task["_prev_streams"][idx].get("tags", {})
                _priority.append(t.get("language", "und"))
        if _priority:
            _matched = smart_reorder_by_priority(audio_streams, [get_language_label(l) for l in _priority])
            if _matched:
                audio_map_order = _matched
    elif len(audio_streams) > 1:
        # First file in batch OR single file — show AF UI
        audio_order_data[task["id"]] = audio_map_order
        audio_order_events[task["id"]] = asyncio.Event()
        # Build detailed audio text with codec, channels, bitrate
        _af_text = "🎛 **Audio Track Order**\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, idx in enumerate(audio_map_order):
            s = audio_streams[idx]
            tags = s.get("tags", {})
            lang = get_language_label(tags.get("language", "und"))
            title = tags.get("title", "")
            codec = s.get("codec_name", "?").upper()
            ch = s.get("channels", "?")
            ch_label = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(ch, f"{ch}ch") if isinstance(ch, int) else f"{ch}ch"
            br = s.get("bit_rate")
            br_str = f"{int(br)//1000}Kbps" if br else ""
            label = f"{title} ({lang})" if title else lang
            detail = f"{codec} {ch_label}"
            if br_str: detail += f" {br_str}"
            prefix = "🔊 **DEFAULT** →" if i == 0 else f"  {i+1}."
            _af_text += f"{prefix} {label} | {detail}\n"
        _af_text += "\n━━━━━━━━━━━━━━━━━━━━\n"
        _af_text += "💡 Tap track to move to #1 (default)\n"
        _af_text += "⏭ Skip = keep current order"
        buttons = _build_audio_order_buttons(audio_streams, audio_map_order, task["id"], user_id)
        await progress_msg.edit(_af_text, reply_markup=buttons)
        try:
            await asyncio.wait_for(audio_order_events[task["id"]].wait(), timeout=300)
        except asyncio.TimeoutError:
            await progress_msg.edit("⏰ AF timed out → default order")
        audio_map_order = audio_order_data.get(task["id"], audio_map_order)
        # Save for batch reuse
        batch_af_decision[user_id] = audio_map_order
        task["_prev_streams"] = audio_streams
        if cancel_tasks.get(task['id']):
            try: await progress_msg.edit("❌ **Cancelled** — files cleaned up")
            except: pass
            _cleanup_files([file_path, download]); return
    else:
        # Single or no audio track — save skip for batch
        # Only skip if we actually found tracks (probe succeeded)
        if user_id not in batch_af_decision and len(audio_streams) > 0:
            batch_af_decision[user_id] = "skip"

    # ---- FULL DOWNLOAD ----
    if cancel_tasks.get(task['id']):
        try: await progress_msg.edit("❌ **Cancelled**")
        except: pass
        return
    await progress_msg.edit(
        f"<b>Download</b>\n"
        f"○○○○○○○○○○ 0%\n"
        f"<b>Speed:</b> -\n"
        f"<b>Estimated:</b> -\n"
        f"/cancel {task['id']}",
        parse_mode="html"
    )
    start_time = time.time()
    task_manager.update_progress(task["id"], status="downloading")
    # Download with cancel support
    async def _dl_progress(current, total, *args):
        if cancel_tasks.get(task['id']):
            raise asyncio.CancelledError("Download cancelled")
        await progress_for_pyrogram(current, total, *args)

    try:
        file_path = await client.download_media(msg, file_name=download,
            progress=_dl_progress,
            progress_args=("📥 Downloading...", progress_msg, start_time, _cancel_data))
    except asyncio.CancelledError:
        logger.info(f"[{task['id']}] Download cancelled by user")
        try: await progress_msg.edit("❌ **Cancelled** — download aborted")
        except: pass
        _cleanup_files([download])
        return
    if cancel_tasks.get(task['id']):
        try: await progress_msg.edit("❌ **Cancelled** — cleaned up")
        except: pass
        _cleanup_files([file_path, download]); return

    # Fallback: if probe found no audio streams, re-probe from full download
    if not audio_streams and file_path and os.path.exists(file_path):
        batch_af_decision.pop(user_id, None)  # Clear stale skip from failed probe
        logger.info(f"[{task['id']}] Re-probing audio from full download...")
        audio_streams = get_audio_streams_info(file_path)
        task["_audio_streams"] = audio_streams
        audio_map_order = list(range(len(audio_streams)))
        logger.info(f"[{task['id']}] Re-probe found {len(audio_streams)} audio streams")
        # Show AF UI if multiple tracks and not already decided
        if len(audio_streams) > 1 and not batch_af_decision.get(user_id):
            audio_order_data[task["id"]] = audio_map_order
            audio_order_events[task["id"]] = asyncio.Event()
            _af_text = "🎛 **Audio Track Order**\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for _i, _idx in enumerate(audio_map_order):
                _s = audio_streams[_idx]
                _tags = _s.get("tags", {})
                _lang = get_language_label(_tags.get("language", "und"))
                _title = _tags.get("title", "")
                _codec = _s.get("codec_name", "?").upper()
                _ch = _s.get("channels", "?")
                _ch_label = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(_ch, f"{_ch}ch") if isinstance(_ch, int) else f"{_ch}ch"
                _label = f"{_title} ({_lang})" if _title else _lang
                _detail = f"{_codec} {_ch_label}"
                _prefix = "🔊 **DEFAULT** →" if _i == 0 else f"  {_i+1}."
                _af_text += f"{_prefix} {_label} | {_detail}\n"
            _af_text += "\n━━━━━━━━━━━━━━━━━━━━\n"
            _af_text += "💡 Tap track to move to #1 (default)\n"
            _af_text += "⏭ Skip = keep current order"
            _btns = _build_audio_order_buttons(audio_streams, audio_map_order, task["id"], user_id)
            await progress_msg.edit(_af_text, reply_markup=_btns)
            try:
                await asyncio.wait_for(audio_order_events[task["id"]].wait(), timeout=300)
            except asyncio.TimeoutError:
                await progress_msg.edit("⏰ AF timed out → default order")
            audio_map_order = audio_order_data.get(task["id"], audio_map_order)
            if cancel_tasks.get(task['id']):
                try: await progress_msg.edit("❌ **Cancelled**")
                except: pass
                _cleanup_files([file_path, download]); return

    task_manager.update_progress(task["id"], progress=10, status="processing")
    # ---- BUILD FFMPEG COMMAND ----
    duration = get_video_duration(file_path)
    compress_level = task.get("compress_level", "skip")
    ratio = COMPRESS_LEVELS.get(compress_level, COMPRESS_LEVELS["skip"])["ratio"]
    ten_bit = await codeflixbots.get_encode_10bit(user_id)
    pix_fmt = "yuv420p10le" if ten_bit else "yuv420p"
    audio_info = AUDIO_CODECS.get(audio_codec_key, AUDIO_CODECS["aac"])
    audio_bitrate = await codeflixbots.get_encode_audio_bitrate(user_id) or "128k"
    audio_channels_key = await codeflixbots.get_encode_audio_channels(user_id)
    audio_samplerate = await codeflixbots.get_encode_audio_samplerate(user_id)
    # Check if watermark should be applied (on/off/ask result)
    _apply_wm = task.get("apply_watermark", True)
    if _apply_wm:
        wm_text = await codeflixbots.get_watermark_text(user_id)
        wm_image_id = await codeflixbots.get_watermark_image(user_id)
        wm_position = await codeflixbots.get_watermark_position(user_id)
        wm_size = await codeflixbots.get_watermark_size(user_id)
        wm_opacity = await codeflixbots.get_watermark_opacity(user_id)
        wm_mode = await codeflixbots.get_watermark_mode(user_id)
    else:
        wm_text = None; wm_image_id = None; wm_position = "top_right"
        wm_size = "medium"; wm_opacity = 0.7; wm_mode = "off"
    sub_mode = await codeflixbots.get_subtitle_mode(user_id)

    # ---- DETERMINE WATERMARK TYPES ----
    needs_text_wm = bool(wm_text) and wm_mode in ("text", "both")
    needs_image_wm = bool(wm_image_id) and wm_mode in ("image", "both")

    # ---- DOWNLOAD WATERMARK IMAGE IF NEEDED ----
    wm_image_path = None
    if needs_image_wm:
        try:
            wm_image_path = await client.download_media(wm_image_id, file_name=f"wm_{task['id']}.png")
            if not wm_image_path or not os.path.exists(wm_image_path):
                logger.warning(f"[{task['id']}] Watermark image download failed")
                needs_image_wm = False; wm_image_path = None
        except Exception as e:
            logger.warning(f"[{task['id']}] Watermark image error: {e}")
            needs_image_wm = False; wm_image_path = None

    vf_parts = []
    if scale: vf_parts.append(f"scale={scale}:flags=lanczos")
    wm_color = await codeflixbots.get_watermark_color(user_id) if _apply_wm else "white"
    wm_style = await codeflixbots.get_watermark_style(user_id) if _apply_wm else "shadow"
    if needs_text_wm: vf_parts.append(build_watermark_filter(wm_text, wm_position, wm_size, wm_opacity, wm_color, wm_style))

    if duration and duration > 0:
        video_bitrate = int(calc_video_bitrate(duration, quality) * ratio)
        max_bitrate = int(calc_max_bitrate(duration, quality) * ratio)
        video_bitrate = max(video_bitrate, MIN_BITRATE.get(quality, 350))
        max_bitrate = max(max_bitrate, int(video_bitrate * 1.4))
        use_bitrate = True
    else:
        use_bitrate = False

    # ----- Performance tuning -----
    ffmpeg_threads = max(4, min(32, (os.cpu_count() or 4) - 1))
    _pools = f"pools={ffmpeg_threads}"

    fast_presets = {"ultrafast", "superfast", "veryfast"}
    mid_presets = {"faster", "fast", "medium"}
    if codec_key == "h265":
        if preset in fast_presets:
            # Fast: skip cutree for speed, keep SAO/deblock for quality
            codec_params = ["-x265-params", f"log-level=error:no-cutree=1:rc-lookahead=15:no-sao=1:bframes=4:ref=1:me=dia:subme=1:{_pools}"]
        elif preset in mid_presets:
            # Balanced: good quality, reasonable speed
            codec_params = ["-x265-params", f"log-level=error:aq-mode=2:rc-lookahead=20:me=hex:subme=2:ref=2:bframes=6:{_pools}"]
        else:
            # Quality: slow/slower/veryslow — maximize quality
            codec_params = ["-x265-params", f"log-level=error:aq-mode=2:rd=3:psy-rd=2.0:psy-rdoq=1.0:rc-lookahead=40:ref=3:bframes=8:{_pools}"]
    elif codec_key == "h264":
        if preset in fast_presets:
            codec_params = []
        else:
            codec_params = ["-tune", "film"]
    else:
        codec_params = []

    inputs = ["-i", file_path]
    if needs_image_wm: inputs += ["-i", wm_image_path]


    metadata_args = await build_metadata_args(user_id, original_title=get_original_title(file_path))
    is_audio_file = not has_video_stream(file_path)
    cmd = ["ffmpeg", "-progress", "pipe:1", "-stats_period", "3", "-nostats", "-threads", str(ffmpeg_threads)] + inputs

    if is_audio_file:
        # ---- AUDIO-ONLY FILE: skip all video processing ----
        cmd += ["-map", "0:a"]
        if audio_info["lib"] == "copy":
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", audio_info["lib"], "-b:a", audio_bitrate]
            ch = AUDIO_CHANNELS.get(audio_channels_key or "original", AUDIO_CHANNELS["original"])
            if ch["val"]: cmd += ["-ac", ch["val"]]
            if audio_samplerate and audio_samplerate not in ("ask", "original"):
                cmd += ["-ar", audio_samplerate]
        cmd += metadata_args + ["-y", encoded]
    elif needs_image_wm:
        # === filter_complex for image watermark ===
        vid_width = RESOLUTION_WIDTHS.get(quality) or get_video_width(file_path)
        wm_ratio = _get_size_ratio(wm_size)
        wm_target_w = max(int(vid_width * wm_ratio), 32)
        if wm_target_w % 2 != 0: wm_target_w += 1
        alpha = max(0.1, min(1.0, wm_opacity))
        overlay_pos = get_overlay_position(wm_position)
        fc_parts = [f"[1:v]scale={wm_target_w}:-1:flags=lanczos,format=rgba,colorchannelmixer=aa={alpha}[wm]"]
        if vf_parts:
            fc_parts.append(f"[0:v]{','.join(vf_parts)}[main]")
            fc_parts.append(f"[main][wm]overlay={overlay_pos}:format=auto[vout]")
        else:
            fc_parts.append(f"[0:v][wm]overlay={overlay_pos}:format=auto[vout]")
        cmd += ["-filter_complex", ";".join(fc_parts), "-map", "[vout]"]
    else:
        cmd += ["-map", "0:v"]

    # Audio mapping — use reordered indices
    for idx in audio_map_order:
        original_index = audio_streams[idx]["index"] if idx < len(audio_streams) else 0
        cmd += ["-map", f"0:{original_index}"]
    if not audio_streams:
        cmd += ["-map", "0:a?"]

    if sub_mode == "copy":
        cmd += ["-map", "0:s?"]
    elif sub_mode == "hardsub":
        vf_parts.insert(0, f"subtitles='{file_path}'")

    vf_str = ",".join(vf_parts) if vf_parts else None
    cmd += ["-c:v", codec_info["lib"], "-preset", preset, "-pix_fmt", pix_fmt]
    if use_bitrate:
        cmd += ["-b:v", f"{video_bitrate}k", "-maxrate", f"{max_bitrate}k", "-bufsize", f"{max_bitrate*2}k"]
    else:
        cmd += ["-crf", str(crf)]
    cmd += codec_params
    if not needs_image_wm and vf_str: cmd += ["-vf", vf_str]
    if audio_info["lib"] == "copy":
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", audio_info["lib"], "-b:a", audio_bitrate]
        ch = AUDIO_CHANNELS.get(audio_channels_key or "original", AUDIO_CHANNELS["original"])
        if ch["val"]: cmd += ["-ac", ch["val"]]
        if audio_samplerate and audio_samplerate not in ("ask", "original"):
            cmd += ["-ar", audio_samplerate]
    if sub_mode == "copy": cmd += ["-c:s", "copy"]
    if not is_audio_file:
        cmd += ["-tag:v", codec_info["tag"]] + metadata_args + ["-y", encoded]

    logger.info(f"[{task['id']}] FFmpeg: {' '.join(cmd[:25])}...")
    
    # ---- ENCODING PROGRESS ----
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL
    )
    task["_ffmpeg_process"] = process  # for cancel kill
    
    # Setup progress tracking
    editor = SafeProgressEditor(progress_msg, user_id, task['id'], min_interval=1.0)
    tracker = ProgressTracker(task['id'], os.path.basename(file_path), total_duration_sec=duration)
    monitor = FFmpegProgressMonitor(process, tracker, duration)
    monitor.start()
    
    encode_start = time.time()
    patience_idx = 0
    last_update = 0
    
    try:
        while True:
            if cancel_tasks.get(task['id']):
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except:
                    pass
                monitor.stop()
                await editor.final_update("❌ Encode Cancelled")
                _cleanup_files([file_path, encoded, wm_image_path])
                return
            
            # Update progress text every 2-3 seconds
            now = time.time()
            if now - last_update >= 3:
                last_update = now
                
                # Format progress with details
                elapsed = tracker.get_elapsed()
                eta = tracker.get_eta()
                
                settings_line = f"{codec_info['label'].split()[-1]} • {quality} • {preset}"
                text = tracker.format_status(emoji="⚙️", title="Encoding", settings_line=settings_line)
                
                # Add patience message for long encodes
                if elapsed > 45:
                    patience = PATIENCE_MSGS[patience_idx % len(PATIENCE_MSGS)]
                    patience_idx += 1
                    text += f"\n\n{patience}"
                
                text += f"\n\n_/cancel {task['id']}_"
                await editor.edit(text)
            
            # Check if process finished
            if process.returncode is not None:
                break
            
            await asyncio.sleep(0.5)
    finally:
        monitor.stop()
    
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
    
    if cancel_tasks.get(task['id']):
        await editor.final_update("❌ Cancelled")
        _cleanup_files([file_path, encoded, wm_image_path])
        return
    
    try:
        await editor.final_update("<b>Encoding Complete ✅</b>\n●●●●●●●●●● 100%", parse_mode="html")
    except:
        pass
    
    if not os.path.exists(encoded) or os.path.getsize(encoded) == 0:
        await editor.final_update("❌ Encoding failed!")
        _cleanup_files([file_path, encoded, wm_image_path])
        return

    # ---- FILE NAME ----
    def _sanitize_output_name(n):
        """Clean filename: no ... or ___ chains."""
        n = re.sub(r'[<>:"/\\|?*]', ' ', n)
        n = re.sub(r'\.{2,}', ' ', n)
        n = re.sub(r'_{2,}', '_', n)
        n = re.sub(r'\s{2,}', ' ', n)
        n = n.strip(' ._-')
        n = re.sub(r'\s*\.\s+', ' ', n)
        return n if n else "encoded"

    # Determine base name based on rename choice
    if rename == "__CAPTION__":
        # "Same as Caption": use original caption text, fallback to original filename
        raw_caption = msg.caption or ""
        if raw_caption:
            base_name = raw_caption.strip()
        else:
            if msg.document and msg.document.file_name: base_name = os.path.splitext(msg.document.file_name)[0]
            elif msg.video and msg.video.file_name: base_name = os.path.splitext(msg.video.file_name)[0]
            elif msg.audio and msg.audio.file_name: base_name = os.path.splitext(msg.audio.file_name)[0]
            else: base_name = f"encoded_{task['id']}"
    elif rename:
        base_name = rename
    else:
        # Keep original filename
        if msg.document and msg.document.file_name: base_name = os.path.splitext(msg.document.file_name)[0]
        elif msg.video and msg.video.file_name: base_name = os.path.splitext(msg.video.file_name)[0]
        elif msg.audio and msg.audio.file_name: base_name = os.path.splitext(msg.audio.file_name)[0]
        else: base_name = f"encoded_{task['id']}"

    replacor_enabled = await codeflixbots.get_replacor_enabled(user_id)
    if replacor_enabled:
        r_strings = await codeflixbots.get_replacor_strings(user_id)
        r_final = await codeflixbots.get_replacor_final(user_id)
        base_name = apply_replacor(base_name, r_strings, r_final)
    
    base_name = _sanitize_output_name(base_name)
    name = base_name + ext

    # ---- METADATA ----
    # Applied directly in main encode ffmpeg command (configurable via settings).

    # ---- THUMB ----
    thumb = None; thumb_id = await codeflixbots.get_thumbnail(user_id)
    if thumb_id:
        try: thumb = await client.download_media(thumb_id, file_name=f"thumb_{task['id']}.jpg")
        except: pass
    
    # Auto-generate thumb from encoded file if no custom thumb
    if not thumb:
        auto_thumb = f"autothumb_{task['id']}.jpg"
        enc_duration = get_media_duration(encoded)
        generated = generate_thumb_at_midpoint(encoded, auto_thumb, enc_duration)
        if generated:
            thumb = generated

    # ---- CAPTION = FILENAME (always synced) ----
    caption = base_name

    task_manager.update_progress(task["id"], progress=90, status="uploading")
    # ---- UPLOAD ----
    await progress_msg.edit(
        f"<b>Upload</b>\n"
        f"○○○○○○○○○○ 0%\n"
        f"<b>File:</b> {os.path.splitext(name)[0][:40]}\n"
        f"<b>Speed:</b> -\n"
        f"<b>Estimated:</b> -\n"
        f"/cancel {task['id']}",
        parse_mode="html"
    )
    while True:
        if cancel_tasks.get(task['id']):
            await progress_msg.edit("❌ Cancelled"); _cleanup_files([file_path, encoded, thumb, wm_image_path]); return
        try:
            st = time.time(); media_pref = await codeflixbots.get_media_preference(user_id)
            _pargs = ("📤 Uploading...", progress_msg, st, f"cancel|{task['id']}|{user_id}")
            out_duration = int(get_media_duration(encoded) or 0)
            if media_pref == "original":
                if msg.video:
                    await client.send_video(chat_id=user_id, video=encoded, caption=caption, thumb=thumb,
                        duration=out_duration, file_name=name,
                        progress=progress_for_pyrogram, progress_args=_pargs, parse_mode=enums.ParseMode.HTML)
                elif msg.audio:
                    await client.send_audio(chat_id=user_id, audio=encoded, caption=caption, thumb=thumb,
                        duration=out_duration, file_name=name,
                        progress=progress_for_pyrogram, progress_args=_pargs, parse_mode=enums.ParseMode.HTML)
                else:
                    await client.send_document(chat_id=user_id, document=encoded, caption=caption, thumb=thumb,
                        file_name=name,
                        progress=progress_for_pyrogram, progress_args=_pargs, parse_mode=enums.ParseMode.HTML)
            elif media_pref == "video":
                await client.send_video(chat_id=user_id, video=encoded, caption=caption, thumb=thumb,
                    duration=out_duration, file_name=name,
                    progress=progress_for_pyrogram, progress_args=_pargs, parse_mode=enums.ParseMode.HTML)
            else:
                await client.send_document(chat_id=user_id, document=encoded, caption=caption, thumb=thumb,
                    file_name=name,
                    progress=progress_for_pyrogram, progress_args=_pargs, parse_mode=enums.ParseMode.HTML)
            break
        except FloodWait as e: await asyncio.sleep(e.value)

    # Delete original message after processing
    try:
        await msg.delete()
    except Exception:
        pass

    await codeflixbots.increment_task_count(user_id, "encode")
    await progress_msg.delete()
    _cleanup_files([file_path, encoded, thumb, wm_image_path, f"autothumb_{task['id']}.jpg", download, f"probe_{task['id']}.mkv", f"wm_{task['id']}.png"])

async def _drain(stream):
    try:
        while True:
            line = await stream.readline()
            if not line: break
    except asyncio.CancelledError: pass
    except: pass

def _cleanup_files(files):
    for f in files:
        try:
            if f and os.path.exists(f): os.remove(f)
        except: pass
