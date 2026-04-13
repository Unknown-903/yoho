import os
import sys
import time
import asyncio
import logging
import subprocess

from bot.modules.media_tools.client_compat import Client
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from bot.helper.media_helper.utils import progress_for_pyrogram, apply_replacor
from bot.core.config_manager import Config
from bot.helper.media_helper.database import codeflixbots

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ================= UPSCALE MODELS =================

UPSCALE_MODELS = {
    "2x": {
        "scale": 2,
        "model": "realesr-animevideov3",
        "label": "⚡ 2x Turbo",
        "eta": "~30s",
    },
    "4x": {
        "scale": 4,
        "model": "realesr-animevideov3",
        "label": "🚀 4x Ultra",
        "eta": "~2 min",
    },
    "4x_photo": {
        "scale": 4,
        "model": "realesrgan-x4plus",
        "label": "🌄 4x Photo HD",
        "eta": "~4 min",
    },
    "2x_anime": {
        "scale": 2,
        "model": "realesrgan-x4plus-anime",
        "label": "🎌 2x Anime AI",
        "eta": "~1 min",
    },
}

SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# ================= ADMIN CHECK =================

def is_admin(user_id):
    return user_id == Config.OWNER_ID or user_id in Config.ADMIN

# ================= FIND REAL-ESRGAN BINARY =================

def get_realesrgan_cmd():
    candidates = [
        "realesrgan-ncnn-vulkan",
        "./realesrgan-ncnn-vulkan",
        "/usr/local/bin/realesrgan-ncnn-vulkan",
        "./Real-ESRGAN/realesrgan-ncnn-vulkan",
        "realesrgan-ncnn-vulkan.exe",
        "./realesrgan-ncnn-vulkan.exe",
    ]
    for cmd in candidates:
        try:
            subprocess.run([cmd, "-h"], capture_output=True, timeout=5)
            return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None

# ================= STATE =================

upscale_wait = {}
cancel_upscale = {}

# ================= /upscale COMMAND =================

@Client.on_message(
    (filters.private | filters.group) &
    filters.command("upscale") &
    filters.reply
)
async def upscale_cmd(client, message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return

    replied = message.reply_to_message

    has_image = replied.photo or (
        replied.document and
        replied.document.file_name and
        any(replied.document.file_name.lower().endswith(ext) for ext in SUPPORTED_FORMATS)
    )

    if not has_image:
        await message.reply_text(
            "❌ Reply to an image (JPG, PNG, WEBP, BMP)\n\n"
            "Usage: Reply to image and send /upscale"
        )
        return

    # Image size limit check — max 10MB
    file_size = 0
    if replied.photo:
        file_size = replied.photo.file_size or 0
    elif replied.document:
        file_size = replied.document.file_size or 0

    max_size = 10 * 1024 * 1024  # 10MB
    if file_size > max_size:
        await message.reply_text(
            f"❌ **Image too large!**\n\n"
            f"📦 Your image: `{round(file_size/1024/1024, 1)} MB`\n"
            f"📏 Max allowed: `10 MB`\n\n"
            f"Please send a smaller image."
        )
        return

    # Image size limit: max 5MB
    MAX_SIZE = 5 * 1024 * 1024  # 5MB
    file_size = 0
    if replied.photo:
        file_size = replied.photo.file_size or 0
    elif replied.document:
        file_size = replied.document.file_size or 0

    if file_size > MAX_SIZE:
        await message.reply_text(
            f"❌ **Image too large!**\n\n"
            f"📦 Your image: `{round(file_size/1024/1024, 2)} MB`\n"
            f"📏 Max allowed: `5 MB`\n\n"
            f"Please compress the image first and try again."
        )
        return

    if not get_realesrgan_cmd():
        await message.reply_text(
            "❌ **Real-ESRGAN not installed!**\n\n"
            "**Linux/VPS install:**\n"
            "`wget https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-ubuntu.zip`\n"
            "`unzip realesrgan-ncnn-vulkan-20220424-ubuntu.zip`\n"
            "`chmod +x realesrgan-ncnn-vulkan`\n"
            "`sudo mv realesrgan-ncnn-vulkan /usr/local/bin/`"
        )
        return

    upscale_wait[user_id] = {"msg": replied, "chat_id": message.chat.id}

    # Group mein hai toh notify karo
    is_group = message.chat.type in ["group", "supergroup"]

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ 2x Turbo", callback_data=f"upscale_model|{user_id}|2x"),
            InlineKeyboardButton("🚀 4x Ultra", callback_data=f"upscale_model|{user_id}|4x"),
        ],
        [
            InlineKeyboardButton("🌄 4x Photo HD", callback_data=f"upscale_model|{user_id}|4x_photo"),
            InlineKeyboardButton("🎌 2x Anime AI", callback_data=f"upscale_model|{user_id}|2x_anime"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data=f"upscale_cancel_pre|{user_id}"),
        ]
    ])

    dm_note = "\n\n📩 _Result will be sent to your DM_" if is_group else ""

    await message.reply_text(
        "🖼 **AI Image Upscaler**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ **2x Fast** — Great quality `~15-30s`\n"
        "🚀 **4x Ultra** — Max detail `~30-60s`\n"
        "🌄 **4x Photo HD** — Real photos `~1-3 min`\n"
        "🎌 **2x Anime** — Anime & art `~1-2 min`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 **Select your mode:**{dm_note}",
        reply_markup=buttons
    )

# ================= MODEL SELECT =================

@Client.on_callback_query(filters.regex("^upscale_model"))
async def upscale_model_select(client, query):
    parts = query.data.split("|")
    _, user_id, model_key = parts
    user_id = int(user_id)

    if query.from_user.id != user_id:
        await query.answer("❌ Ye tumhara task nahi hai!", show_alert=True)
        return

    data = upscale_wait.pop(user_id, None)
    if not data:
        await query.answer("Session expired. Send /upscale again.", show_alert=True)
        return

    model_info = UPSCALE_MODELS[model_key]
    task_id = int(time.time() * 1000)
    cancel_upscale[task_id] = False

    await query.message.edit_text(
        f"🔄 Starting... {model_info['label']}"
    )

    asyncio.create_task(
        run_upscale(client, data["msg"], query.message, task_id, user_id, model_info)
    )

# ================= CANCEL (pre-task) =================

@Client.on_callback_query(filters.regex("^upscale_cancel_pre"))
async def upscale_cancel_pre(client, query):
    _, user_id = query.data.split("|")
    user_id = int(user_id)
    if query.from_user.id != user_id:
        await query.answer("❌ Ye tumhara task nahi hai!", show_alert=True)
        return
    upscale_wait.pop(user_id, None)
    await query.message.edit_text("❌ Upscale cancelled.")


# ================= CANCEL (during task) =================

@Client.on_callback_query(filters.regex("^upscale_cancel_pre"))
async def cancel_upscale_pre(client, query):
    _, user_id = query.data.split("|")
    user_id = int(user_id)

    if query.from_user.id != user_id:
        await query.answer("❌ Ye tumhara task nahi hai!", show_alert=True)
        return

    upscale_wait.pop(user_id, None)
    await query.message.edit_text("❌ Upscale cancelled.")


@Client.on_callback_query(filters.regex("^cancel_upscale"))
async def cancel_upscale_cb(client, query):
    _, task_id, user_id = query.data.split("|")
    task_id = int(task_id)
    user_id = int(user_id)

    if query.from_user.id != user_id:
        await query.answer("❌ Ye tumhara task nahi hai!", show_alert=True)
        return

    cancel_upscale[task_id] = True
    await query.answer("❌ Cancelling...")

# ================= UPSCALE RUNNER =================

async def run_upscale(client, msg, progress_msg, task_id, user_id, model_info):
    scale = model_info["scale"]
    model = model_info["model"]
    label = model_info["label"]

    input_file = f"upscale_in_{task_id}.png"
    output_file = f"upscale_out_{task_id}.png"
    file_path = None

    try:
        # ---------------- DOWNLOAD ----------------
        await progress_msg.edit(
            f"<b>Download</b>\n○○○○○○○○○○ 0%\n<b>Speed:</b> -\n/cancel {task_id}",
            parse_mode="html"
        )

        start_time = time.time()
        logger.info(f"[{task_id}] Download started | user={user_id}")
        file_path = await client.download_media(
            msg,
            file_name=input_file,
            progress=progress_for_pyrogram,
            progress_args=("📥 Downloading...", progress_msg, start_time)
        )

        if cancel_upscale.get(task_id):
            logger.info(f"[{task_id}] Cancelled during download")
            await progress_msg.edit("❌ Cancelled")
            return

        if not file_path or not os.path.exists(file_path):
            logger.error(f"[{task_id}] Download failed — file_path={file_path}")
            await progress_msg.edit("❌ Download failed")
            return

        file_size_mb = round(os.path.getsize(file_path) / 1024 / 1024, 2)
        logger.info(f"[{task_id}] Download complete | size={file_size_mb}MB | model={model} scale={scale}x")

        # ---------------- UPSCALE ----------------
        await progress_msg.edit(
            f"<b>Upscale</b> • {label}\n⏳ Starting AI processing...\n/cancel {task_id}",
            parse_mode="html"
        )

        realesrgan_cmd = get_realesrgan_cmd()
        cmd = [
            realesrgan_cmd,
            "-i", file_path,
            "-o", output_file,
            "-n", model,
            "-s", str(scale),
            "-f", "png",
        ]

        logger.info(f"[{task_id}] Upscale process started | cmd={' '.join(cmd)}")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # Animated dots + ETA while upscaling
        dots = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        dot_idx = 0
        elapsed = 0
        eta = model_info.get("eta", "please wait")
        last_msg_update = 0

        while True:
            if cancel_upscale.get(task_id):
                process.kill()
                logger.info(f"[{task_id}] Upscale cancelled by user={user_id}")
                await progress_msg.edit("❌ Upscale Cancelled")
                return

            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
                break
            except asyncio.TimeoutError:
                elapsed += 1
                dot_idx = (dot_idx + 1) % len(dots)
                now = time.time()
                # Update message every 5 seconds to avoid FloodWait
                if now - last_msg_update >= 5:
                    last_msg_update = now
                    mins, secs = divmod(elapsed, 60)
                    elapsed_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                    try:
                        await progress_msg.edit(
                            f"<b>Upscale</b> • {label}\n"
                            f"{dots[dot_idx]} Processing...\n"
                            f"<b>Elapsed:</b> {elapsed_str}\n"
                            f"<b>Estimated:</b> {eta}\n"
                            f"/cancel {task_id}",
                            parse_mode="html"
                        )
                    except FloodWait as e:
                        last_msg_update = time.time() + e.value
                    except:
                        pass

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            err = stderr.decode("utf-8", errors="ignore")[:300]
            logger.error(f"[{task_id}] Upscale failed: {err}")
            await progress_msg.edit(f"❌ Upscale failed!\n\n`{err}`")
            return

        if not os.path.exists(output_file):
            await progress_msg.edit("❌ Output file not found")
            return

        orig_size = os.path.getsize(file_path)
        new_size = os.path.getsize(output_file)
        logger.info(
            f"[{task_id}] Upscale complete | "
            f"input={round(orig_size/1024,1)}KB "
            f"output={round(new_size/1024,1)}KB "
            f"ratio={round(new_size/orig_size, 1)}x larger"
        )

        # ---------------- UPLOAD ----------------

        # Apply metadata if enabled
        caption = (
            f"✅ **Upscaled {scale}x** — {label}\n"
            f"📦 Original: `{round(orig_size/1024, 1)} KB`\n"
            f"📦 Upscaled: `{round(new_size/1024, 1)} KB`"
        )

        if await codeflixbots.get_metadata(user_id):
            user_title = await codeflixbots.get_title(user_id) or ""
            author = await codeflixbots.get_author(user_id) or ""
            artist = await codeflixbots.get_artist(user_id) or ""
            metadata_code = await codeflixbots.get_metadata_code(user_id) or ""
            if user_title or author or artist or metadata_code:
                metadata_text = f"\n\n**📝 Metadata:**\n"
                if user_title:
                    metadata_text += f"Title: `{user_title}`\n"
                if author:
                    metadata_text += f"Author: `{author}`\n"
                if artist:
                    metadata_text += f"Artist: `{artist}`\n"
                if metadata_code:
                    metadata_text += f"Encoder: `{metadata_code}`\n"
                caption += metadata_text

        # Apply replacor if enabled
        r_enabled = await codeflixbots.get_replacor_enabled(user_id)
        if r_enabled:
            r_strings = await codeflixbots.get_replacor_strings(user_id)
            r_final = await codeflixbots.get_replacor_final(user_id)
            if r_strings and r_final:
                caption = apply_replacor(caption, r_strings, r_final)

        start_time = time.time()
        await progress_msg.edit(
            f"<b>Upload</b>\n○○○○○○○○○○ 0%\n<b>Speed:</b> -\n/cancel {task_id}",
            parse_mode="html"
        )

        # Group mein command tha toh DM mein bhejo — group spam nahi hoga
        send_chat_id = user_id  # hamesha DM

        while True:
            if cancel_upscale.get(task_id):
                await progress_msg.edit("❌ Upload Cancelled")
                return
            try:
                media_pref = await codeflixbots.get_media_preference(user_id)
                _pargs = ("📤 Uploading...", progress_msg, start_time)
                if media_pref == "original" and msg.photo:
                    await client.send_photo(chat_id=send_chat_id, photo=output_file,
                        caption=caption)
                else:
                    await client.send_document(chat_id=send_chat_id, document=output_file,
                        caption=caption, progress=progress_for_pyrogram, progress_args=_pargs)
                break
            except FloodWait as e:
                await asyncio.sleep(e.value)

        await progress_msg.delete()
        await codeflixbots.increment_task_count(user_id, "upscale")
        total_time = round(time.time() - start_time)
        logger.info(f"[{task_id}] ✅ Task complete | user={user_id} | total_time={total_time}s")

    except Exception as e:
        logger.exception(f"[{task_id}] Unexpected error | user={user_id} | error={e}")
        try:
            await progress_msg.edit(f"❌ Error: {str(e)[:200]}")
        except:
            pass

    finally:
        cancel_upscale.pop(task_id, None)
        cleaned = []
        for f in [input_file, output_file, file_path]:
            try:
                if f and os.path.exists(f):
                    os.remove(f)
                    cleaned.append(f)
            except:
                pass
        if cleaned:
            logger.info(f"[{task_id}] Cleanup done | files={cleaned}")
