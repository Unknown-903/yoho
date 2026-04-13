import os
import sys
import time
import asyncio
import logging
from collections import deque

from bot.modules.media_tools.client_compat import Client
from pyrogram import filters, ContinuePropagation, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from bot.helper.media_helper.utils import progress_for_pyrogram, apply_replacor, build_metadata_args, check_watermark_for_process
from bot.helper.media_helper.database import codeflixbots
from bot.helper.media_helper.permissions import is_admin as _perm_is_admin, can_access_premium_feature
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

def is_admin(user_id):
    return user_id == Config.OWNER_ID or _perm_is_admin(user_id)

# ================= MERGE QUALITY =================

MERGE_QUALITY = {
    "copy": {
        "label": "⚡ Fast Copy",
        "desc": "No re-encode · fastest · same quality",
        "encode": False,
    },
    "low": {
        "label": "🟢 Low Compress",
        "desc": "~10% smaller · best quality",
        "encode": True,
        "crf": 26,
    },
    "medium": {
        "label": "🟡 Medium Compress",
        "desc": "~30% smaller · good quality",
        "encode": True,
        "crf": 28,
    },
    "high": {
        "label": "🟠 High Compress",
        "desc": "~50% smaller · decent quality",
        "encode": True,
        "crf": 31,
    },
    "best": {
        "label": "🔴 Best Compress",
        "desc": "~70% smaller · max compression",
        "encode": True,
        "crf": 35,
    },
}

# Encode quality options (resolution + CRF)
ENCODE_QUALITY = {
    "480p":  {"scale": "854:480",   "crf": 26, "label": "🎬 480p"},
    "720p":  {"scale": "1280:720",  "crf": 24, "label": "📺 720p"},
    "1080p": {"scale": "1920:1080", "crf": 22, "label": "🔥 1080p"},
    "4k":    {"scale": "3840:2160", "crf": 20, "label": "💎 4K"},
}

# Funny jokes for progress bar
MERGE_JOKES = [
    "☕ Server chai pi raha hai, thoda ruko...",
    "🐢 FFmpeg ka kachua race mein hai...",
    "🔧 Bits aur bytes ko suljha rahe hain...",
    "🎭 CPU ko motivational speech de rahe hain...",
    "🍕 Files merge ho rahi hain, pizza order karo...",
    "🧩 Video ke tukde jod rahe hain, patience raho...",
    "🚀 Rocket ki speed se process ho raha hai... (nahi)...",
    "😅 Bot thaka nahi hai, bas slow hai...",
    "🎪 Data circus mein juggling ho rahi hai...",
    "⏳ Ek minute mein ho jayega... (shayad)...",
    "🤖 AI mehnat kar raha hai, chai de do...",
    "🌀 Files ka chakravyuh tod rahe hain...",
]

# ================= STATE =================

merge_sessions = {}   # user_id -> {"files": [], "msg_ids": []}
batch_merge_state = {}  # user_id -> {"files": [], "waiting": True}
merge_pending = {}    # user_id -> task (waiting for rename/encode input)
merge_queue = asyncio.Queue()
queue_list = deque()
active_tasks = {}
workers_started = False
cancel_tasks = {}
_upload_edit_times = {}  # upload progress throttle
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
            task = await asyncio.wait_for(merge_queue.get(), timeout=1.0)
            active_tasks[task["id"]] = task
            _uid = task.get("user", 0)
            acquire_lock(_uid, "merge", task_id=task["id"], file_name="merge batch")
            task_manager.register(task["id"], _uid, "merge", file_name="merge batch")
            try:
                await run_merge(client, task)
            except asyncio.CancelledError:
                logger.warning(f"[{task['id']}] Merge task cancelled")
                raise
            except Exception as e:
                logger.error(f"Merge worker error: {e}")
            finally:
                release_lock(_uid, "merge")
                task_manager.complete(task["id"])
                active_tasks.pop(task["id"], None)
                try:
                    queue_list.remove(task)
                except:
                    pass
                merge_queue.task_done()
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            logger.info("Worker shutdown requested")
            break


async def merge_cleanup():
    """Call this during bot shutdown to cleanup merge tasks"""
    global shutdown_event
    if shutdown_event:
        shutdown_event.set()
    
    for task in list(worker_tasks):
        if not task.done():
            task.cancel()
    
    if worker_tasks:
        await asyncio.gather(*worker_tasks, return_exceptions=True)


# ================= /merge COMMAND =================

@Client.on_message(
    (filters.private | filters.group) &
    filters.command("merge")
)
async def merge_cmd(client, message):
    user_id = message.from_user.id

    # Check premium access (admin or premium user)
    if not await can_access_premium_feature(user_id):
        await message.reply_text(
            "❌ **Premium Feature**\n\n"
            "Merge is available for:\n"
            "✅ Admin/Owner\n"
            "✅ Premium Members\n\n"
            "📞 Contact @SharkToonsIndia for premium access!"
        )
        return

    if user_id in merge_sessions:
        count = len(merge_sessions[user_id]["files"])
        await message.reply_text(
            f"⚠️ Already in merge session!\n\n"
            f"📦 Files collected: `{count}`\n\n"
            f"Send more files or /mdone to merge\n"
            f"Use /mergecancel to cancel session"
        )
        return

    merge_sessions[user_id] = {
        "files": [],
        "chat_id": message.chat.id,
        "is_group": message.chat.type in ["group", "supergroup"],
    }

    await message.reply_text(
        "🎬 **Merge Session Started**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📤 Ab apni video files bhejo ek ek karke\n"
        "✅ Sab files bhejne ke baad `/mdone` bhejo\n"
        "❌ Cancel karne ke liye `/mergecancel` bhejo\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ _Files same format/resolution mein honi chahiye_"
    )
    await start_workers(client)


# ================= /mergebatch COMMAND =================

@Client.on_message(
    (filters.private | filters.group) &
    filters.command("mergebatch")
)
async def batch_merge_cmd(client, message):
    user_id = message.from_user.id

    if not is_admin(user_id):
        await message.reply_text("❌ Sirf owner aur admins use kar sakte hain")
        return

    if user_id in batch_merge_state:
        count = len(batch_merge_state[user_id]["files"])
        await message.reply_text(
            f"⚠️ Already in batch merge session!\n\n"
            f"📦 Files collected: `{count}`\n\n"
            f"Send more files or /mdone to start\n"
            f"Use /mergebatchcancel to cancel session"
        )
        return

    batch_merge_state[user_id] = {
        "files": [],
        "waiting": True,
    }

    await message.reply_text(
        "🎬 **Batch Merge Session Started**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📤 Ab apni video files bhejo ek ek karke\n"
        "✅ Sab files bhejne ke baad `/mdone` bhejo\n"
        "❌ Cancel karne ke liye `/mergebatchcancel` bhejo\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ _Files same format/resolution mein honi chahiye_"
    )


# ================= COLLECT FILES =================

@Client.on_message(
    (filters.private | filters.group) &
    (filters.video | filters.document),
    group=2
)
async def collect_merge_files(client, message):
    user_id = message.from_user.id if message.from_user else None

    if not user_id or (user_id not in merge_sessions and user_id not in batch_merge_state):
        raise ContinuePropagation

    if not is_admin(user_id):
        raise ContinuePropagation

    file = message.video or message.document
    if not file:
        raise ContinuePropagation

    # Document ho toh check karo video/audio file hai
    if message.document:
        name = message.document.file_name or ""
        if not any(name.lower().endswith(ext) for ext in
                   [".mkv", ".mp4", ".avi", ".mov", ".flv", ".ts", ".m4v",
                    ".mp3", ".aac", ".m4a", ".opus", ".flac", ".ogg"]):
            raise ContinuePropagation

    if user_id in merge_sessions:
        session = merge_sessions[user_id]
    else:
        session = batch_merge_state[user_id]

    session["files"].append(message)
    count = len(session["files"])

    await message.reply_text(
        f"✅ File `{count}` added\n\n"
        f"📦 Total: `{count}` file(s)\n"
        f"Send more or /mdone to merge"
    )


# ================= /mdone COMMAND =================

@Client.on_message(
    (filters.private | filters.group) &
    filters.command("mdone")
)
async def merge_done(client, message):
    user_id = message.from_user.id

    if not is_admin(user_id):
        return

    # Prefer active merge session, else use batch merge state
    if user_id in merge_sessions:
        session = merge_sessions[user_id]
    elif user_id in batch_merge_state and batch_merge_state[user_id].get("waiting"):
        state = batch_merge_state.pop(user_id)
        session = {
            "files": state.get("files", []),
            "chat_id": message.chat.id,
            "is_group": message.chat.type in ["group", "supergroup"],
        }
        merge_sessions[user_id] = session
    else:
        await message.reply_text("❌ No active merge session\nSend /merge or /mergebatch to start")
        return

    files = session.get("files", [])

    if len(files) < 2:
        await message.reply_text(
            f"❌ Kam se kam 2 files chahiye merge ke liye\n\n"
            f"Abhi sirf `{len(files)}` file hai"
        )
        return

    # Quality select karo
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ Fast Copy", callback_data=f"merge_quality|{user_id}|copy"),
        ],
        [
            InlineKeyboardButton("🟢 Low", callback_data=f"merge_quality|{user_id}|low"),
            InlineKeyboardButton("🟡 Medium", callback_data=f"merge_quality|{user_id}|medium"),
        ],
        [
            InlineKeyboardButton("🟠 High", callback_data=f"merge_quality|{user_id}|high"),
            InlineKeyboardButton("🔴 Best", callback_data=f"merge_quality|{user_id}|best"),
        ],
        [
            InlineKeyboardButton("🎬 Encode 480p", callback_data=f"merge_quality|{user_id}|enc_480p"),
            InlineKeyboardButton("📺 Encode 720p", callback_data=f"merge_quality|{user_id}|enc_720p"),
        ],
        [
            InlineKeyboardButton("🔥 Encode 1080p", callback_data=f"merge_quality|{user_id}|enc_1080p"),
            InlineKeyboardButton("💎 Encode 4K", callback_data=f"merge_quality|{user_id}|enc_4k"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data=f"merge_cancel_pre|{user_id}"),
        ]
    ])

    await message.reply_text(
        f"🎬 **Ready to Merge**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 Files: `{len(files)}`\n\n"
        f"⚡ **Fast Copy** — No re-encode · fastest\n"
        f"🟢 **Low** — ~10% smaller · best quality\n"
        f"🟡 **Medium** — ~30% smaller · good quality\n"
        f"🟠 **High** — ~50% smaller · decent quality\n"
        f"🔴 **Best** — ~70% smaller · max compression\n"
        f"🎬 **Encode** — Re-encode to selected resolution\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 **Select quality:**",
        reply_markup=buttons
    )


# ================= /mergebatchcancel COMMAND =================

@Client.on_message(
    (filters.private | filters.group) &
    filters.command("mergebatchcancel")
)
async def merge_batch_cancel_cmd(client, message):
    user_id = message.from_user.id

    if not is_admin(user_id):
        return

    if user_id not in batch_merge_state:
        await message.reply_text("❌ No active batch merge session")
        return

    session = batch_merge_state.pop(user_id)
    count = len(session.get("files", []))
    await message.reply_text(
        f"❌ Batch merge session cancelled\n"
        f"📦 `{count}` file(s) discarded"
    )


# ================= /mergecancel COMMAND =================

@Client.on_message(
    (filters.private | filters.group) &
    filters.command("mergecancel")
)
async def merge_cancel_cmd(client, message):
    user_id = message.from_user.id

    if not is_admin(user_id):
        return

    if user_id not in merge_sessions:
        await message.reply_text("❌ No active merge session")
        return

    session = merge_sessions.pop(user_id)
    count = len(session["files"])
    await message.reply_text(
        f"❌ Merge session cancelled\n"
        f"📦 `{count}` file(s) discarded"
    )


# ================= QUALITY SELECT =================

@Client.on_callback_query(filters.regex("^merge_quality"))
async def merge_quality_select(client, query):
    _, user_id, quality = query.data.split("|")
    user_id = int(user_id)

    if query.from_user.id != user_id:
        await query.answer("❌ Ye tumhara task nahi hai!", show_alert=True)
        return

    session = merge_sessions.pop(user_id, None)
    if not session:
        await query.answer("Session expired. Send /merge again.", show_alert=True)
        return

    # Encode option handle karo
    if quality.startswith("enc_"):
        res = quality.replace("enc_", "")  # "480p", "720p" etc
        eq = ENCODE_QUALITY[res]
        quality_info = {
            "label": f"🎬 Encode {res.upper()}",
            "desc": f"Re-encode to {res}",
            "encode": True,
            "crf": eq["crf"],
            "scale": eq["scale"],
            "is_encode": True,
        }
    else:
        quality_info = MERGE_QUALITY[quality]
        quality_info["is_encode"] = False

    task = {
        "id": int(time.time() * 1000),
        "user": user_id,
        "files": session["files"],
        "quality": quality,
        "quality_info": quality_info,
        "is_group": session.get("is_group", False),
        "rename": None,
    }

    # Step 1: Rename puchho
    merge_pending[user_id] = task

    await query.message.edit_text(
        f"✏️ **Rename Merged File?**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 Files: `{len(task['files'])}`\n"
        f"🎬 Quality: {quality_info['label']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✏️  Rename", callback_data=f"merge_rename|{user_id}|yes"),
                InlineKeyboardButton("⏭️  Skip", callback_data=f"merge_rename|{user_id}|skip"),
            ],
            [InlineKeyboardButton("❌  Cancel", callback_data=f"merge_cancel_pre|{user_id}")]
        ])
    )


# ================= RENAME STEP =================

@Client.on_callback_query(filters.regex("^merge_rename"))
async def merge_rename_cb(client, query):
    parts = query.data.split("|")
    _, user_id, action = parts
    user_id = int(user_id)

    if query.from_user.id != user_id:
        await query.answer("❌ Ye tumhara task nahi hai!", show_alert=True)
        return

    task = merge_pending.get(user_id)
    if not task:
        await query.answer("Session expired.", show_alert=True)
        return

    if action == "yes":
        # Rename input wait
        task["waiting_rename"] = True
        merge_pending[user_id] = task
        await query.message.edit_text(
            "✏️ **Enter New File Name**\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📝 Naya naam bhejo\n"
            "Example: `My Merged Video`\n\n"
            "_(Extension automatically .mkv hogi)_\n"
            "━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️  Skip Rename", callback_data=f"merge_rename|{user_id}|skip")]
            ])
        )
    else:
        # Skip rename — go to encode step
        task["waiting_rename"] = False
        task["rename"] = None
        merge_pending[user_id] = task
        await show_encode_step(query, user_id, task)


async def show_encode_step(query, user_id, task):
    quality_info = task["quality_info"]
    await query.message.edit_text(
        f"🎬 **Encode After Merge?**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 Files: `{len(task['files'])}`\n"
        f"🗜️ Quality: {quality_info['label']}\n"
        f"✏️ Rename: `{task.get('rename') or 'Skip'}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎬  480p", callback_data=f"merge_encode|{user_id}|enc_480p"),
                InlineKeyboardButton("📺  720p", callback_data=f"merge_encode|{user_id}|enc_720p"),
            ],
            [
                InlineKeyboardButton("🔥  1080p", callback_data=f"merge_encode|{user_id}|enc_1080p"),
                InlineKeyboardButton("💎  4K",    callback_data=f"merge_encode|{user_id}|enc_4k"),
            ],
            [
                InlineKeyboardButton("⏭️  Skip Encode", callback_data=f"merge_encode|{user_id}|skip"),
            ],
            [InlineKeyboardButton("❌  Cancel", callback_data=f"merge_cancel_pre|{user_id}")]
        ])
    )


@Client.on_callback_query(filters.regex("^merge_encode"))
async def merge_encode_cb(client, query):
    parts = query.data.split("|")
    _, user_id, enc = parts
    user_id = int(user_id)

    if query.from_user.id != user_id:
        await query.answer("❌ Ye tumhara task nahi hai!", show_alert=True)
        return

    task = merge_pending.pop(user_id, None)
    if not task:
        await query.answer("Session expired.", show_alert=True)
        return

    # Apply encode quality if selected
    if enc != "skip" and enc.startswith("enc_"):
        res = enc.replace("enc_", "")
        eq = ENCODE_QUALITY[res]
        task["quality_info"] = {
            "label": f"🎬 Encode {res.upper()}",
            "desc": f"Re-encode to {res}",
            "encode": True,
            "crf": eq["crf"],
            "scale": eq["scale"],
            "is_encode": True,
        }
        task["quality"] = enc

    queue_list.append(task)
    cancel_tasks[task["id"]] = False

    pos = merge_queue.qsize() + 1
    rename_str = f"`{task.get('rename')}`" if task.get('rename') else "Default"
    enc_str = task["quality_info"]["label"]

    await query.message.edit_text(
        f"📥 **Added to Merge Queue**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 Files: `{len(task['files'])}`\n"
        f"🎬 Quality: {enc_str}\n"
        f"✏️ Rename: {rename_str}\n"
        f"📌 Position: `{pos}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

    await merge_queue.put(task)


# ================= GET RENAME TEXT =================

@Client.on_message(
    (filters.private | filters.group) &
    filters.text &
    ~filters.command(["merge", "done", "mergecancel", "encode", "compress",
                      "upscale", "select", "queue", "logs", "restart",
                      "status", "settings", "start", "help"]),
    group=3
)
async def merge_rename_input(client, message):
    user_id = message.from_user.id
    task = merge_pending.get(user_id)
    if not task or not task.get("waiting_rename"):
        return

    rename = message.text.strip()
    if not rename:
        await message.reply_text("❌ Empty naam — phir bhejo")
        return

    task["rename"] = rename
    task["waiting_rename"] = False
    merge_pending[user_id] = task

    # Show encode step via fake query-like edit
    await message.reply_text(
        f"✅ Rename set: `{rename}`\n\n"
        f"Ab encode option select karo 👇",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎬  480p", callback_data=f"merge_encode|{user_id}|enc_480p"),
                InlineKeyboardButton("📺  720p", callback_data=f"merge_encode|{user_id}|enc_720p"),
            ],
            [
                InlineKeyboardButton("🔥  1080p", callback_data=f"merge_encode|{user_id}|enc_1080p"),
                InlineKeyboardButton("💎  4K",    callback_data=f"merge_encode|{user_id}|enc_4k"),
            ],
            [
                InlineKeyboardButton("⏭️  Skip Encode", callback_data=f"merge_encode|{user_id}|skip"),
            ],
            [InlineKeyboardButton("❌  Cancel", callback_data=f"merge_cancel_pre|{user_id}")]
        ])
    )


# ================= PRE-CANCEL =================

@Client.on_callback_query(filters.regex("^merge_cancel_pre"))
async def merge_cancel_pre(client, query):
    _, user_id = query.data.split("|")
    user_id = int(user_id)

    if query.from_user.id != user_id:
        await query.answer("❌ Ye tumhara task nahi hai!", show_alert=True)
        return

    merge_sessions.pop(user_id, None)
    await query.message.edit_text("❌ Merge cancelled.")


# ================= TASK CANCEL =================

@Client.on_callback_query(filters.regex("^merge_cancel[|]"))
async def merge_cancel_task(client, query):
    _, task_id, user_id = query.data.split("|")
    task_id = int(task_id)
    user_id = int(user_id)
    caller_id = query.from_user.id

    # Task owner cancel kar sakta hai
    if caller_id == user_id:
        pass
    # Owner kisi ka bhi cancel kar sakta hai
    elif caller_id == Config.OWNER_ID:
        pass
    # Admin sirf apna cancel kar sakta hai
    else:
        await query.answer("❌ Ye tumhara task nahi hai!", show_alert=True)
        return

    cancel_tasks[task_id] = True
    await query.answer("❌ Cancelling...")


# ================= /mtasks COMMAND =================

@Client.on_message(
    (filters.private | filters.group) &
    filters.command("mtasks")
)
async def merge_tasks_cmd(client, message):
    if not is_admin(message.from_user.id):
        return

    if not active_tasks and merge_queue.empty():
        return await message.reply_text("✅ No active merge tasks")

    text = "🎬 **Merge Tasks**\n\n"
    for task_id, task in active_tasks.items():
        text += (
            f"⚙️ **Running**\n"
            f"👤 User: `{task['user']}`\n"
            f"📦 Files: `{len(task['files'])}`\n"
            f"🎬 Quality: {task['quality_info']['label']}\n"
            f"🆔 ID: `{task_id}`\n\n"
        )

    if not merge_queue.empty():
        text += f"📦 Queue: `{merge_queue.qsize()}` pending\n"

    await message.reply_text(text)


# ================= RUN MERGE =================

async def run_merge(client, task):
    user_id = task["user"]
    files = task["files"]
    task_id = task["id"]
    quality = task["quality"]
    quality_info = task["quality_info"]

    cancel_hint = f"\n\n_/cancel {task_id}_"

    os.makedirs("downloads", exist_ok=True)
    downloaded = []
    progress_msg = None

    try:
        # ---------------- DOWNLOAD ALL FILES ----------------
        progress_msg = await files[0].reply_text(
            f"<b>Download</b>\n○○○○○○○○○○ 0%\n<b>Files:</b> 0/{len(files)}\n/cancel {task_id}",
            parse_mode="html"
        )

        for i, msg in enumerate(files):
            if cancel_tasks.get(task_id):
                await progress_msg.edit("❌ Cancelled")
                return

            dl_path = f"downloads/merge_{task_id}_{i}.mkv"
            start_time = time.time()

            try:
                await progress_msg.edit(
                    f"<b>Download</b>\n○○○○○○○○○○\n<b>Files:</b> {i+1}/{len(files)}\n/cancel {task_id}",
                    parse_mode="html"
                )
            except:
                pass

            file_path = await client.download_media(
                msg,
                file_name=dl_path,
                progress=progress_for_pyrogram,
                progress_args=(f"📥 File {i+1}/{len(files)}", progress_msg, start_time)
            )

            if not file_path or not os.path.exists(file_path):
                await progress_msg.edit(f"❌ Download failed for file {i+1}")
                return

            downloaded.append(file_path)
            logger.info(f"[{task_id}] Downloaded {i+1}/{len(files)}: {file_path}")

        if cancel_tasks.get(task_id):
            await progress_msg.edit("❌ Cancelled")
            return

        logger.info(f"[{task_id}] All {len(downloaded)} files downloaded")

        # ---------------- CREATE CONCAT LIST ----------------
        concat_file = f"downloads/concat_{task_id}.txt"
        with open(concat_file, "w") as f:
            for path in downloaded:
                f.write(f"file '{os.path.abspath(path)}'\n")

        output = f"downloads/merged_{task_id}.mkv"

        # ---------------- MERGE ----------------
        await progress_msg.edit(
            f"<b>Merge</b>\n○○○○○○○○○○ 0%\n<b>Files:</b> {len(files)}\n<b>Estimated:</b> -\n/cancel {task_id}",
            parse_mode="html"
        )

        # ------------ WATERMARK CHECK ------------
        wm_filter = None
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
                    logger.info(f"[{task_id}] Watermark will be applied: {wm_text}")
        except Exception as e:
            logger.info(f"[{task_id}] Watermark skipped: {e}")

        if quality_info["encode"]:
            # Encode with H.265 — with or without scale
            vf_parts = []
            if quality_info.get("is_encode") and "scale" in quality_info:
                vf_parts.append(f"scale={quality_info['scale']}:flags=lanczos")
            if wm_filter:
                vf_parts.append(wm_filter)
            vf_args = ["-vf", ",".join(vf_parts)] if vf_parts else []

            cmd = [
                "ffmpeg",
                "-progress", "pipe:1",
                "-nostats",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file,
                "-map", "0",
            ] + vf_args + [
                "-c:v", "libx265",
                "-preset", "veryfast",
                "-crf", str(quality_info["crf"]),
                "-x265-params", "log-level=error",
                "-c:a", "aac",
                "-b:a", "128k",
                "-ac", "2",
                "-c:s", "copy",
                "-y",
                output
            ]
        else:
            # Fast copy — no re-encode
            cmd = [
                "ffmpeg",
                "-progress", "pipe:1",
                "-nostats",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file,
                "-map", "0",
                "-c", "copy",
                "-y",
                output
            ]

        logger.info(f"[{task_id}] Merge started | quality={quality}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        
        # Setup progress tracking
        editor = SafeProgressEditor(progress_msg, user_id, task_id, min_interval=2.0)
        tracker = ProgressTracker(task_id, f"merge_{len(files)}files", total_duration_sec=None)
        
        merge_start = time.time()
        last_update = 0
        last_progress = 0
        
        try:
            while True:
                if cancel_tasks.get(task_id):
                    process.kill()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except:
                        pass
                    await editor.final_update("❌ Merge Cancelled")
                    return
                
                line = await process.stdout.readline()
                if not line:
                    break
                
                text = line.decode("utf-8", errors="ignore")
                
                # Update progress estimate based on ffmpeg output
                if "out_time=" in text:
                    # Simple estimation - increment steadily
                    last_progress = min(last_progress + 1, 95)
                
                # Update display every 2 seconds
                now = time.time()
                if now - last_update >= 2:
                    last_update = now
                    
                    elapsed = tracker.get_elapsed()
                    text_msg = f"🎬 Merging {quality_info['label']}\n\n"
                    
                    # Format progress bar
                    bar_str = "⬢" * (last_progress // 10) + "⬡" * (10 - last_progress // 10)
                    text_msg += f"{bar_str} {last_progress}%\n"
                    text_msg += f"⏳ {format_time(elapsed)}\n"
                    text_msg += f"📄 Merging {len(files)} files"
                    text_msg += f"\n\n_/cancel {task_id}_"
                    
                    await editor.edit(text_msg)
        finally:
            pass

        try:
            await asyncio.wait_for(process.wait(), timeout=120)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        
        if cancel_tasks.get(task_id):
            await editor.final_update("❌ Merge Cancelled")
            return
        
        logger.info(f"[{task_id}] Merge complete")
        
        try:
            await editor.final_update(f"🎬 Merging {quality_info['label']}\n\n⬢⬢⬢⬢⬢⬢⬢⬢⬢⬢ 100% ✅")
        except:
            pass


        # ---------------- AUDIO REORDER ----------------
        streams, order = await probe_and_reorder_audio(
            client, output, user_id, task_id, progress_msg, timeout=300
        )
        if order is None:  # User cancelled
            return

        audio_args = build_audio_map_args(streams, order) if streams else ["-map", "0:a?"]

        # ---------------- WATERMARK (copy mode) ----------------
        # If merge was copy mode and watermark is on, apply watermark in a separate pass
        if wm_filter and not quality_info["encode"]:
            wm_output = f"downloads/wm_{task_id}.mkv"
            wm_cmd = [
                "ffmpeg", "-i", output,
                "-vf", wm_filter,
                "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                "-c:a", "copy", "-c:s", "copy",
                "-y", wm_output
            ]
            try:
                await progress_msg.edit("💧 Applying watermark...")
                wm_proc = await asyncio.create_subprocess_exec(
                    *wm_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                await wm_proc.wait()
                if os.path.exists(wm_output) and os.path.getsize(wm_output) > 0:
                    os.remove(output)
                    output = wm_output
                    logger.info(f"[{task_id}] Watermark applied (copy mode)")
            except Exception as e:
                logger.info(f"[{task_id}] Watermark pass failed: {e}")

        # ---------------- METADATA ----------------
        metadata_args = await build_metadata_args(user_id, original_title=get_original_title(output))

        if metadata_args:
            meta_file = f"downloads/meta_{task_id}.mkv"
            meta_cmd = [
                "ffmpeg",
                "-i", output,
                "-map", "0:v",
            ] + audio_args + [
                "-map", "0:s?",
                "-c", "copy",
            ] + metadata_args + [
                "-y", meta_file
            ]

            await progress_msg.edit("🗜️ Adding metadata...")
            meta_proc = await asyncio.create_subprocess_exec(
                *meta_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await meta_proc.wait()

            output_final = meta_file if os.path.exists(meta_file) else output
        else:
            output_final = output

        # ---------------- THUMB ----------------
        thumb = None
        thumb_id = await codeflixbots.get_thumbnail(user_id)
        if thumb_id:
            try:
                thumb = await client.download_media(
                    thumb_id,
                    file_name=f"downloads/thumb_{task_id}.jpg"
                )
            except:
                thumb = None

        # ---------------- UPLOAD ----------------
        total_size = sum(os.path.getsize(f) for f in downloaded)
        merged_size = os.path.getsize(output_final)
        # Use rename if set
        rename = task.get("rename")
        if rename:
            name = rename.strip()
            if not name.lower().endswith(".mkv"):
                name = name + ".mkv"
            replacor_enabled = await codeflixbots.get_replacor_enabled(user_id)
            if replacor_enabled:
                r_strings = await codeflixbots.get_replacor_strings(user_id)
                r_final = await codeflixbots.get_replacor_final(user_id)
                name = apply_replacor(name, r_strings, r_final)
        else:
            name = f"merged_{task_id}.mkv"

        default_caption = (
            f"🎬 **Merged** — {quality_info['label']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Files merged: `{len(files)}`\n"
            f"📦 Total input: `{round(total_size/1024/1024, 2)} MB`\n"
            f"📦 Output: `{round(merged_size/1024/1024, 2)} MB`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 `{name}`"
        )

        caption_format = await codeflixbots.get_caption_format(user_id)
        if caption_format == "as_original":
            original_caption = files[0].caption if files and hasattr(files[0], 'caption') else None
            caption = original_caption or name
        else:
            custom = await codeflixbots.get_caption(user_id)
            caption = custom if custom else default_caption

        await progress_msg.edit(
            f"<b>Upload</b>\n○○○○○○○○○○ 0%\n<b>Speed:</b> -\n<b>Estimated:</b> -\n/cancel {task_id}",
            parse_mode="html"
        )

        start_time = time.time()
        logger.info(f"[{task_id}] Upload started")

        async def upload_progress(current, total, ud_type, message, start):
            """Upload progress with cancel button always visible"""
            now = time.time()
            diff = now - start
            if diff <= 0:
                return
            if current == total:
                return

            # Throttle — har 5 sec mein edit
            msg_id = message.id
            last = _upload_edit_times.get(msg_id, 0)
            if now - last < 5:
                return
            _upload_edit_times[msg_id] = now

            percentage = current * 100 / total if total else 0
            speed = current / diff if diff else 0
            eta = (total - current) / speed if speed else 0
            filled = "⬢" * int(percentage / 10)
            empty = "⬡" * (10 - int(percentage / 10))

            def fmt(size):
                for unit in ["B","KB","MB","GB"]:
                    if size < 1024:
                        return f"{round(size,2)} {unit}"
                    size /= 1024
                return f"{round(size,2)} TB"

            def tfmt(ms):
                s = int(ms/1000)
                m, s = divmod(s, 60)
                h, m = divmod(m, 60)
                return f"{h}h {m}m {s}s" if h else f"{m}m {s}s" if m else f"{s}s"

            text = (
                f"📤 Uploading...\n\n"
                f"{filled}{empty} {round(percentage,2)}%\n\n"
                f"📦 {fmt(current)} / {fmt(total)}\n"
                f"⚡ {fmt(speed)}/s\n"
                f"⏳ {tfmt(eta*1000)}"
            )
            try:
                await message.edit(
                    text,
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("❌ Cancel", callback_data=f"merge_cancel|{task_id}|{user_id}")]]
                    )
                )
            except FloodWait as e:
                _upload_edit_times[msg_id] = now + e.value
            except:
                pass

        while True:
            if cancel_tasks.get(task_id):
                await progress_msg.edit("❌ Upload Cancelled")
                return
            try:
                await client.send_document(
                    chat_id=user_id,  # hamesha DM
                    document=output_final,
                    file_name=name,
                    caption=caption,
                    thumb=thumb if thumb else None,
                    progress=upload_progress,
                    progress_args=("📤 Uploading...", progress_msg, start_time),
                    parse_mode=enums.ParseMode.HTML
                )
                break
            except FloodWait as e:
                await asyncio.sleep(e.value)

        # Delete original message after processing
        try:
            await msg.delete()
        except Exception:
            pass

        logger.info(f"[{task_id}] Merge task complete")
        await codeflixbots.increment_task_count(user_id, "merge")
        await progress_msg.delete()

    except Exception as e:
        logger.error(f"[{task_id}] Error: {e}")
        try:
            await progress_msg.edit(f"❌ Error: {str(e)[:200]}")
        except:
            pass

    finally:
        cancel_tasks.pop(task_id, None)
        # Cleanup all files
        all_files = downloaded + [
            f"downloads/concat_{task_id}.txt",
            f"downloads/merged_{task_id}.mkv",
            f"downloads/meta_{task_id}.mkv",
            f"downloads/thumb_{task_id}.jpg",
        ]
        for f in all_files:
            try:
                if f and os.path.exists(f):
                    os.remove(f)
            except:
                pass
