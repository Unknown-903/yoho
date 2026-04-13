import os
import sys
import time
import asyncio
import logging
import multiprocessing
from collections import deque

from bot.modules.media_tools.client_compat import Client
from pyrogram import filters, ContinuePropagation, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from bot.helper.media_helper.utils import progress_for_pyrogram, apply_replacor, build_metadata_args, check_watermark_for_process
from bot.helper.media_helper.auth import auth_chats
from bot.helper.media_helper.database import codeflixbots
from bot.helper.media_helper.permissions import is_owner, is_admin as _perm_is_admin, is_authorized_chat, can_access_premium_feature
from bot.core.config_manager import Config
from bot.helper.media_helper.command_lock import acquire_lock, release_lock, is_locked
from bot.helper.media_helper.task_manager import task_manager
from bot.helper.media_helper.cleanup import cleanup_task, safe_delete_files
from bot.helper.media_helper.audio_reorder import probe_and_reorder_audio, build_audio_map_args
from lang_map import get_original_title
from bot.helper.media_helper.progress import (
    ProgressTracker, SafeProgressEditor, FFmpegProgressMonitor,
    format_bytes, format_time
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


# ================= WATERMARK FILTER =================

def build_watermark_filter(wm_text, wm_position, wm_size, wm_opacity, wm_color="white", wm_style="shadow"):
    """Build FFmpeg drawtext filter for watermark."""
    preset_sizes = {"small": "18", "medium": "28", "large": "42", "xlarge": "56"}
    if wm_size in preset_sizes:
        fontsize_expr = preset_sizes[wm_size]
    elif str(wm_size).endswith("%"):
        try:
            pct = int(str(wm_size).rstrip("%")) / 100
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

def is_admin(user_id):
    return user_id == Config.OWNER_ID or _perm_is_admin(user_id)

# ================= COMPRESS LEVELS =================

COMPRESS_LEVELS = {
    "low": {
        "label": "🟢 Low",
        "ratio": 0.75,   # original size ka 75% — ~25% smaller
        "desc": "~25% smaller · best quality"
    },
    "medium": {
        "label": "🟡 Medium",
        "ratio": 0.55,   # ~45% smaller
        "desc": "~45% smaller · good quality"
    },
    "high": {
        "label": "🟠 High",
        "ratio": 0.38,   # ~62% smaller
        "desc": "~60% smaller · decent quality"
    },
    "best": {
        "label": "🔴 Best",
        "ratio": 0.25,   # ~75% smaller
        "desc": "~75% smaller · max compression"
    },
}


def get_video_duration(file_path):
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             file_path],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip())
    except:
        return None


def get_video_resolution(file_path):
    """Video resolution detect karo — bitrate floor ke liye"""
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0",
             file_path],
            capture_output=True, text=True, timeout=30
        )
        parts = result.stdout.strip().split(",")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except:
        pass
    return None, None


def get_resolution_floor(width, height):
    """Resolution se minimum bitrate floor nikalo"""
    if height is None:
        return 300
    if height <= 480:
        return 350
    elif height <= 720:
        return 700
    elif height <= 1080:
        return 1400
    else:
        return 3000


def calc_compress_bitrate(file_size_bytes, duration_sec, ratio, width=None, height=None, audio_kbps=128):
    """Original file size aur ratio se target bitrate nikalo — floor enforce karo"""
    target_bytes = file_size_bytes * ratio
    target_bits = target_bytes * 8
    total_kbps = (target_bits / duration_sec) / 1000
    video_kbps = int(total_kbps - audio_kbps)

    # Resolution-aware floor
    floor = get_resolution_floor(width, height)
    video_kbps = max(video_kbps, floor)

    # maxrate hamesha target se 40% zyada — kabhi target se kam nahi
    max_kbps = int(video_kbps * 1.4)
    return video_kbps, max_kbps

# ================= QUEUE =================

compress_queue = asyncio.Queue()
queue_list = deque()
active_tasks = {}
workers_started = False
cancel_tasks = {}
compress_wait = {}  # user_id -> {"msg": message}
worker_tasks = set()  # Store worker task references for cleanup
shutdown_event = None  # Set during bot shutdown

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
            task = await asyncio.wait_for(compress_queue.get(), timeout=1.0)
            active_tasks[task["id"]] = task
            _uid = task.get("user", 0)
            _fname = ""
            try:
                _m = task.get("msg")
                if _m: _fname = getattr(getattr(_m, "document", None), "file_name", "") or ""
            except: pass
            acquire_lock(_uid, "compress", task_id=task["id"], file_name=_fname)
            task_manager.register(task["id"], _uid, "compress", file_name=_fname)
            try:
                await run_compress(client, task)
            except asyncio.CancelledError:
                logger.warning(f"[{task['id']}] Compress task cancelled")
                raise
            except Exception as e:
                logger.error(f"Worker error: {e}")
            finally:
                release_lock(_uid, "compress")
                task_manager.complete(task["id"])
                active_tasks.pop(task["id"], None)
                try:
                    queue_list.remove(task)
                except:
                    pass
                compress_queue.task_done()
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            logger.info("Worker shutdown requested")
            break


async def compress_cleanup():
    """Call this during bot shutdown to cleanup compress tasks"""
    global shutdown_event
    if shutdown_event:
        shutdown_event.set()
    
    for task in list(worker_tasks):
        if not task.done():
            task.cancel()
    
    if worker_tasks:
        await asyncio.gather(*worker_tasks, return_exceptions=True)


# ================= BATCH COMPRESS STATE =================

batch_compress_state = {}  # user_id -> {"files": [messages], "level": str}

@Client.on_message(
    (filters.private | filters.group) &
    filters.command("compress") &
    ~filters.reply
)
async def batch_compress_cmd(client, message):
    user_id = message.from_user.id

    # Sirf owner aur admins use kar sakte hain
    if not is_admin(user_id):
        await message.reply_text("❌ Sirf owner aur admins use kar sakte hain")
        return

    # Group mein auth check
    if message.chat.type in ["group", "supergroup"]:
        if message.chat.id not in auth_chats:
            await message.reply_text("❌ This group is not authorized")
            return

    # Initialize batch state
    batch_compress_state[user_id] = {"files": [], "waiting": True}
    await start_workers(client)
    
    await message.reply_text(
        "📥 **Batch Compress Mode**\n\n"
        "Send me multiple video files, then send `/cdone` to start compression.\n"
        "All files will use the same compression level.\n\n"
        "Files collected: 0"
    )

@Client.on_message((filters.private | filters.group) & filters.command("cdone"))
async def process_batch_compress(client, message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.reply_text("❌ Only admins can use batch processing.")
        return
    if message.chat.type in ["group", "supergroup"]:
        if message.chat.id not in auth_chats:
            await message.reply_text("❌ This group is not authorized for batch processing.")
            return
    
    if user_id not in batch_compress_state:
        await message.reply_text("❌ No batch compress session active. Send /compress first.")
        return  # Silently return if not in compress batch mode
    
    state = batch_compress_state[user_id]
    files = state.get("files", [])
    if not files:
        return
    
    # Ask for compression level once
    compress_wait[user_id] = {"batch_files": files, "batch_mode": True}
    
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 Low", callback_data=f"compress_level|{user_id}|low"),
            InlineKeyboardButton("🟡 Medium", callback_data=f"compress_level|{user_id}|medium"),
        ],
        [
            InlineKeyboardButton("🟠 High", callback_data=f"compress_level|{user_id}|high"),
            InlineKeyboardButton("🔴 Best", callback_data=f"compress_level|{user_id}|best"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data=f"compress_cancel_pre|{user_id}"),
        ]
    ])
    await message.reply_text("**🗜️ Select Compression Level** (for all files)", reply_markup=buttons)
    batch_compress_state.pop(user_id, None)  # Clean up batch state

@Client.on_message(
    (filters.private | filters.group) & (filters.video | filters.document),
    group=2
)
async def batch_compress_file_handler(client, message):
    user_id = message.from_user.id if message.from_user else None
    if not user_id or user_id not in batch_compress_state or not batch_compress_state[user_id].get("waiting"):
        raise ContinuePropagation
    
    # Add file to batch
    state = batch_compress_state[user_id]
    state["files"].append(message)

# ================= /compress COMMAND =================

@Client.on_message(
    (filters.private | filters.group) &
    filters.command("compress") &
    filters.reply
)
async def compress_cmd(client, message):
    user_id = message.from_user.id

    # Check premium access (admin or premium user)
    if not await can_access_premium_feature(user_id):
        await message.reply_text(
            "❌ **Premium Feature**\n\n"
            "Compression is available for:\n"
            "✅ Admin/Owner\n"
            "✅ Premium Members\n\n"
            "📞 Contact @SharkToonsIndia for premium access!"
        )
        return

    # Group mein auth check
    if message.chat.type in ["group", "supergroup"]:
        if message.chat.id not in auth_chats:
            await message.reply_text("❌ This group is not authorized")
            return

    replied = message.reply_to_message

    if not (replied.video or replied.document):
        await message.reply_text("❌ Reply to a video or file")
        return

    is_group = message.chat.type in ["group", "supergroup"]
    compress_wait[user_id] = {"msg": replied, "is_group": is_group}

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 Low", callback_data=f"compress_level|{user_id}|low"),
            InlineKeyboardButton("🟡 Medium", callback_data=f"compress_level|{user_id}|medium"),
        ],
        [
            InlineKeyboardButton("🟠 High", callback_data=f"compress_level|{user_id}|high"),
            InlineKeyboardButton("🔴 Best", callback_data=f"compress_level|{user_id}|best"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data=f"compress_cancel_pre|{user_id}"),
        ]
    ])

    dm_note = "\n\n📩 _Result will be sent to your DM_" if is_group else ""

    await message.reply_text(
        "🗜️ **Video Compressor**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🟢 **Low** — ~25% smaller · best quality\n"
        "🟡 **Medium** — ~45% smaller · good quality\n"
        "🟠 **High** — ~60% smaller · decent quality\n"
        "🔴 **Best** — ~75% smaller · max compression\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 **Select compression level:**{dm_note}",
        reply_markup=buttons
    )

    await start_workers(client)


# ================= LEVEL SELECT =================

@Client.on_callback_query(filters.regex("^compress_level"))
async def compress_level_select(client, query):
    _, user_id, level = query.data.split("|")
    user_id = int(user_id)

    if query.from_user.id != user_id:
        await query.answer("❌ Ye tumhara task nahi hai!", show_alert=True)
        return

    data = compress_wait.pop(user_id, None)
    if not data:
        await query.answer("Session expired. Send /compress again.", show_alert=True)
        return

    level_info = COMPRESS_LEVELS[level]
    batch_mode = data.get("batch_mode", False)
    files = data.get("batch_files", [data.get("msg")]) if batch_mode else [data.get("msg")]
    
    tasks_added = []
    for i, msg in enumerate(files):
        task_id = int(time.time() * 1000) + i  # Unique IDs
        task = {
            "id": task_id,
            "user": user_id,
            "level": level,
            "ratio": level_info["ratio"],
            "label": level_info["label"],
            "msg": msg,
            "name": query.from_user.first_name,
            "is_group": data.get("is_group", False),
        }
        queue_list.append(task)
        cancel_tasks[task["id"]] = False
        tasks_added.append(task)
        await compress_queue.put(task)
    
    file_count = len(tasks_added)
    pos_start = len(queue_list) - file_count + 1
    pos_end = len(queue_list)
    
    await query.message.edit_text(
        f"📥 Added {file_count} files to Queue\n\n"
        f"{level_info['label']} — {level_info['desc']}\n"
        f"📌 Position: {pos_start}-{pos_end}"
    )


# ================= PRE-CANCEL =================

@Client.on_callback_query(filters.regex("^compress_cancel_pre"))
async def compress_cancel_pre(client, query):
    _, user_id = query.data.split("|")
    user_id = int(user_id)

    if query.from_user.id != user_id:
        await query.answer("❌ Ye tumhara task nahi hai!", show_alert=True)
        return

    compress_wait.pop(user_id, None)
    await query.message.edit_text("❌ Compress cancelled.")


# ================= CANCEL =================

@Client.on_callback_query(filters.regex("^compress_cancel[|]"))
async def compress_cancel(client, query):
    _, task_id, user_id = query.data.split("|")
    task_id = int(task_id)
    user_id = int(user_id)
    caller_id = query.from_user.id

    # Task owner — cancel kar sakta hai
    if caller_id == user_id:
        pass
    # Owner — kisi ka bhi cancel kar sakta hai
    elif caller_id == Config.OWNER_ID:
        pass
    # Admin — sirf apna cancel kar sakta hai, dusre admin ka nahi
    else:
        await query.answer("❌ Ye tumhara task nahi hai!", show_alert=True)
        return

    cancel_tasks[task_id] = True
    await query.answer("❌ Cancelling...")


# ================= /ctasks COMMAND =================

@Client.on_message(
    (filters.private | filters.group) & filters.command("ctasks")
)
async def compress_tasks_cmd(client, message):
    if not is_admin(message.from_user.id):
        return

    if not active_tasks and compress_queue.empty():
        return await message.reply_text("✅ No active compress tasks")

    text = "🗜️ **Compress Tasks**\n\n"

    for task_id, task in active_tasks.items():
        text += (
            f"⚙️ **Running**\n"
            f"👤 User: `{task['user']}`\n"
            f"📊 Level: {task['label']}\n"
            f"🆔 ID: `{task_id}`\n\n"
        )

    if not compress_queue.empty():
        text += f"📦 Queue: `{compress_queue.qsize()}` pending\n"

    await message.reply_text(text)


# ================= RUN COMPRESS =================

async def run_compress(client, task):
    msg = task["msg"]
    user_id = task["user"]
    ratio = task["ratio"]
    label = task["label"]
    task_id = task["id"]
    ext = await codeflixbots.get_video_extension(user_id) or "mkv"
    ext = f".{ext.lstrip('.')}"

    cancel_hint = f"\n\n_/cancel {task_id}_"

    os.makedirs("downloads", exist_ok=True)
    download = f"downloads/comp_in_{task_id}.mkv"
    output = f"downloads/comp_out_{task_id}{ext}"
    file_path = None
    thumb = None

    try:
        # ---------------- DOWNLOAD ----------------
        progress_msg = await msg.reply_text(
            f"<b>Download</b>\n○○○○○○○○○○ 0%\n<b>Speed:</b> -\n<b>Estimated:</b> -\n/cancel {task_id}",
            parse_mode="html"
        )

        # ---------------- SPEED TUNING ----------------
        ffmpeg_threads = max(2, min(16, (os.cpu_count() or 2) // 2))

        start_time = time.time()
        logger.info(f"[{task_id}] Compress download started | user={user_id}")

        file_path = await client.download_media(
            msg,
            file_name=download,
            progress=progress_for_pyrogram,
            progress_args=("📥 Downloading...", progress_msg, start_time)
        )

        metadata_args = await build_metadata_args(user_id, original_title=get_original_title(file_path))

        logger.info(f"[{task_id}] Download complete: {file_path}")

        if cancel_tasks.get(task_id):
            await progress_msg.edit("❌ Download Cancelled")
            return


        # ---------------- AUDIO REORDER ----------------
        streams, order = await probe_and_reorder_audio(
            client, file_path, user_id, task_id, progress_msg, timeout=300
        )
        if order is None:  # User cancelled
            return

        # ---------------- SIZE + DURATION + RESOLUTION ----------------
        orig_size = os.path.getsize(file_path)
        duration = get_video_duration(file_path)
        width, height = get_video_resolution(file_path)
        logger.info(f"[{task_id}] Resolution={width}x{height} | orig={round(orig_size/1024/1024,1)}MB")

        if duration and duration > 0:
            video_kbps, max_kbps = calc_compress_bitrate(orig_size, duration, ratio, width, height)
            logger.info(f"[{task_id}] Duration={duration:.1f}s | bitrate={video_kbps}k | max={max_kbps}k")
            use_bitrate = True
        else:
            logger.warning(f"[{task_id}] Duration detect nahi hui, CRF fallback")
            crf_map = {"low": 26, "medium": 28, "high": 31, "best": 35}
            fallback_crf = crf_map.get(task["level"], 28)
            use_bitrate = False

        # ---------------- COMPRESS ----------------
        await progress_msg.edit(
            f"<b>Compress</b> • {label}\n○○○○○○○○○○ 0%\n<b>Speed:</b> -\n<b>Estimated:</b> -\n/cancel {task_id}",
            parse_mode="html"
        )

        # Compress level ke hisaab se scale — high/best pe resolution bhi ghataao speed ke liye
        # Audio map from reorder
        audio_args = build_audio_map_args(streams, order) if streams else ["-map", "0:a?"]

        scale_map = {
            "low":    None,          # original resolution rakho
            "medium": None,          # original resolution rakho
            "high":   "1280:720",    # 720p pe le aao
            "best":   "854:480",     # 480p pe le aao
        }
        scale = scale_map.get(task["level"], None)
        vf_filter = f"scale={scale}:flags=lanczos" if scale else None

        # ------------ WATERMARK CHECK ------------
        try:
            wm_apply = await codeflixbots.get_watermark_apply(user_id)
            if wm_apply == "on":
                wm_text = await codeflixbots.get_watermark_text(user_id)
                if wm_text:
                    wm_pos = await codeflixbots.get_watermark_position(user_id) or "top_right"
                    wm_size = await codeflixbots.get_watermark_size(user_id) or "medium"
                    wm_opacity = await codeflixbots.get_watermark_opacity(user_id) or 0.7
                    wm_color = await codeflixbots.get_watermark_color(user_id) or "white"
                    wm_style = await codeflixbots.get_watermark_style(user_id) or "shadow"
                    wm_filter = build_watermark_filter(wm_text, wm_pos, wm_size, wm_opacity, wm_color, wm_style)
                    if vf_filter:
                        vf_filter = f"{vf_filter},{wm_filter}"
                    else:
                        vf_filter = wm_filter
                    logger.info(f"[{task_id}] Watermark applied: {wm_text}")
        except Exception as e:
            logger.info(f"[{task_id}] Watermark skipped: {e}")

        def build_cmd(extra_video_args):
            base = [
                "ffmpeg",
                "-progress", "pipe:1",
                "-stats_period", "3",
                "-nostats",
                "-nostdin",
                "-threads", str(ffmpeg_threads),
                "-i", file_path,
                "-map", "0:v",
            ] + audio_args + [
                "-map", "0:s?",
            ]
            if vf_filter:
                base += ["-vf", vf_filter]
            base += [
                "-c:v", "libx265",
                "-preset", "ultrafast",
            ]
            base += extra_video_args
            base += [
                "-x265-params", "log-level=error:no-cutree=1:rc-lookahead=20",
                "-c:a", "aac",
                "-b:a", "128k",
                "-c:s", "copy",
            ] + metadata_args + [
                "-y",
                output
            ]
            return base

        if use_bitrate:
            cmd = build_cmd([
                "-b:v", f"{video_kbps}k",
                "-maxrate", f"{max_kbps}k",
                "-bufsize", f"{max_kbps * 2}k",
            ])
        else:
            cmd = build_cmd(["-crf", str(fallback_crf)])

        logger.info(f"[{task_id}] Compress started | level={task['level']}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        
        # Setup progress tracking with duration
        editor = SafeProgressEditor(progress_msg, user_id, task_id, min_interval=2.0)
        tracker = ProgressTracker(task_id, os.path.basename(file_path), total_duration_sec=duration)
        monitor = FFmpegProgressMonitor(process, tracker, duration)
        monitor.start()
        
        compress_start = time.time()
        last_update = 0
        
        try:
            while True:
                if cancel_tasks.get(task_id):
                    process.kill()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except:
                        pass
                    monitor.stop()
                    await editor.final_update("❌ Compress Cancelled")
                    return
                
                # Update progress every 2-3 seconds
                now = time.time()
                if now - last_update >= 2:
                    last_update = now
                    
                    # Format progress with details
                    elapsed = tracker.get_elapsed()
                    text = f"🗜️ Compressing ({label})\n\n"
                    text += tracker.format_status(emoji="🗜️", title="").replace("Processing", label)
                    text += f"\n\n_/cancel {task_id}_"
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
        
        if cancel_tasks.get(task_id):
            await editor.final_update("❌ Compress Cancelled")
            return
        
        try:
            await editor.final_update(f"🗜️ Compressing ({label})\n\n⬢⬢⬢⬢⬢⬢⬢⬢⬢⬢ 100% ✅")
        except:
            pass

        if not os.path.exists(output):
            await progress_msg.edit("❌ Compress failed — output not found")
            return

        new_size = os.path.getsize(output)
        saved = orig_size - new_size
        saved_pct = round((saved / orig_size) * 100, 1) if orig_size else 0

        # Agar compressed bada ho gaya toh original use karo
        if new_size >= orig_size:
            logger.warning(f"[{task_id}] Compressed ({new_size}) >= original ({orig_size}) — original use kar raha hoon")
            os.remove(output)
            # Original ko hi output maano
            output = file_path
            new_size = orig_size
            saved = 0
            saved_pct = 0
            already_compressed_note = "\n⚠️ _File already compressed — original bheja gaya_"
        else:
            already_compressed_note = ""

        # ---------------- RENAME ----------------
        if msg.document and msg.document.file_name:
            name = msg.document.file_name
        elif msg.video and msg.video.file_name:
            name = msg.video.file_name
        else:
            name = f"compressed_{task_id}{ext}"

        name = os.path.splitext(name)[0] + ext

        replacor_enabled = await codeflixbots.get_replacor_enabled(user_id)
        if replacor_enabled:
            r_strings = await codeflixbots.get_replacor_strings(user_id)
            r_final = await codeflixbots.get_replacor_final(user_id)
            name = apply_replacor(name, r_strings, r_final)

        # ---------------- METADATA ----------------
        # Metadata has been injected into the main ffmpeg pass for speed.
        output_final = output

        # ---------------- THUMB ----------------
        thumb_id = await codeflixbots.get_thumbnail(user_id)
        if thumb_id:
            try:
                thumb = await client.download_media(
                    thumb_id,
                    file_name=f"downloads/thumb_{task_id}.jpg"
                )
            except:
                thumb = None

        # Get duration for video/audio
        duration = None
        try:
            import subprocess
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", output_final],
                capture_output=True, text=True, timeout=30)
            duration = int(float(result.stdout.strip()))
        except Exception:
            duration = None

        # ---------------- UPLOAD ----------------
        default_caption = (
            f"🗜️ **Compressed** — {label}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Original: `{round(orig_size/1024/1024, 2)} MB`\n"
            f"📦 Compressed: `{round(new_size/1024/1024, 2)} MB`\n"
            f"✅ Saved: `{round(saved/1024/1024, 2)} MB ({saved_pct}%)`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 `{name}`"
            f"{already_compressed_note}"
        )

        caption_format = await codeflixbots.get_caption_format(user_id)
        if caption_format == "as_original":
            caption = msg.caption or name
        else:
            custom = await codeflixbots.get_caption(user_id)
            caption = custom if custom else default_caption

        await progress_msg.edit(
            f"<b>Upload</b>\n○○○○○○○○○○ 0%\n<b>Speed:</b> -\n<b>Estimated:</b> -\n/cancel {task_id}",
            parse_mode="html"
        )
        start_time = time.time()
        logger.info(f"[{task_id}] Upload started")

        while True:
            if cancel_tasks.get(task_id):
                await progress_msg.edit("❌ Upload Cancelled")
                return
            try:
                media_pref = await codeflixbots.get_media_preference(user_id)
                _pargs = ("📤 Uploading...", progress_msg, start_time)
                if media_pref == "original":
                    if msg.video:
                        await client.send_video(chat_id=user_id, video=output_final,
                            caption=caption, thumb=thumb if thumb else None, duration=duration,
                            progress=progress_for_pyrogram, progress_args=_pargs, parse_mode=enums.ParseMode.HTML)
                    elif msg.audio:
                        await client.send_audio(chat_id=user_id, audio=output_final,
                            caption=caption, thumb=thumb if thumb else None, duration=duration,
                            progress=progress_for_pyrogram, progress_args=_pargs, parse_mode=enums.ParseMode.HTML)
                    else:
                        await client.send_document(chat_id=user_id, document=output_final,
                            file_name=name, caption=caption, thumb=thumb if thumb else None,
                            progress=progress_for_pyrogram, progress_args=_pargs, parse_mode=enums.ParseMode.HTML)
                elif media_pref == "video":
                    await client.send_video(chat_id=user_id, video=output_final,
                        caption=caption, thumb=thumb if thumb else None, duration=duration,
                        progress=progress_for_pyrogram, progress_args=_pargs, parse_mode=enums.ParseMode.HTML)
                else:
                    await client.send_document(chat_id=user_id, document=output_final,
                        file_name=name, caption=caption, thumb=thumb if thumb else None,
                        progress=progress_for_pyrogram, progress_args=_pargs, parse_mode=enums.ParseMode.HTML)
                break
            except FloodWait as e:
                await asyncio.sleep(e.value)

        # Delete original message after processing
        try:
            await msg.delete()
        except Exception:
            pass

        logger.info(f"[{task_id}] Task complete | saved={saved_pct}%")
        await codeflixbots.increment_task_count(user_id, "compress")
        await progress_msg.delete()

    except Exception as e:
        logger.error(f"[{task_id}] Error: {e}")
        try:
            await progress_msg.edit(f"❌ Error: {str(e)[:200]}")
        except:
            pass

    finally:
        cancel_tasks.pop(task_id, None)
        for f in [file_path, output, f"downloads/meta_{task_id}{ext}", thumb]:
            try:
                if f and os.path.exists(f):
                    os.remove(f)
            except:
                pass
