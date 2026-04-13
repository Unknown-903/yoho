"""
Subtitle and audio track extraction from video files.
Adapted from ENCODING-BOT (Harshitmr3030).
"""
import os
import asyncio
import logging
import subprocess

from bot.modules.media_tools.client_compat import Client
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from helper.utils import progress_for_pyrogram, humanbytes, apply_replacor
from bot.helper.media_helper.database import codeflixbots
from bot.helper.media_helper.permissions import is_admin as _perm_is_admin
from lang_map import get_language_label
from bot.core.config_manager import Config

logger = logging.getLogger(__name__)


def is_admin(user_id):
    return user_id == Config.OWNER_ID or _perm_is_admin(user_id)


def get_media_info(file_path):
    """Get detailed stream info using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", file_path],
            capture_output=True, text=True, timeout=30
        )
        import json
        return json.loads(result.stdout)
    except Exception as e:
        logger.error(f"ffprobe error: {e}")
        return None


def list_streams(info):
    """Parse ffprobe output into readable stream list."""
    if not info or "streams" not in info:
        return []
    streams = []
    for s in info["streams"]:
        idx = s.get("index", 0)
        codec_type = s.get("codec_type", "unknown")
        codec_name = s.get("codec_name", "")
        raw_lang = s.get("tags", {}).get("language", "und")
        lang = get_language_label(raw_lang)
        title = s.get("tags", {}).get("title", "")
        
        if codec_type == "video":
            w = s.get("width", "?")
            h = s.get("height", "?")
            streams.append(f"🎬 #{idx} Video — {codec_name} ({w}x{h})")
        elif codec_type == "audio":
            ch = s.get("channels", "?")
            streams.append(f"🔊 #{idx} Audio — {codec_name} [{lang}] {title} ({ch}ch)")
        elif codec_type == "subtitle":
            streams.append(f"📝 #{idx} Subtitle — {codec_name} [{lang}] {title}")
    return streams


async def extract_stream(input_path, output_path, stream_index, codec_type):
    """Extract a specific stream from a video file."""
    if codec_type == "subtitle":
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-map", f"0:{stream_index}",
            "-c:s", "srt" if output_path.endswith(".srt") else "copy",
            output_path
        ]
    elif codec_type == "audio":
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-map", f"0:{stream_index}",
            "-c:a", "copy",
            output_path
        ]
    else:
        return False

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        logger.error(f"Extract failed: {stderr.decode()}")
        return False
    return True


@Client.on_message(
    (filters.private | filters.group) &
    filters.command("extract") &
    filters.reply
)
async def extract_cmd(client, message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return await message.reply_text("❌ Only owner/admin can use this.")

    replied = message.reply_to_message
    if not (replied.video or replied.document):
        return await message.reply_text("❌ Reply to a video/document file.")

    media = replied.video or replied.document
    file_name = media.file_name or "video.mkv"
    file_size = media.file_size or 0

    sts = await message.reply_text(
        f"📥 **Downloading:** `{file_name}`\n"
        f"📦 **Size:** `{humanbytes(file_size)}`"
    )

    dl_path = f"downloads/{user_id}/{file_name}"
    os.makedirs(os.path.dirname(dl_path), exist_ok=True)

    try:
        start = __import__("time").time()
        dl = await replied.download(
            file_name=dl_path,
            progress=progress_for_pyrogram,
            progress_args=("📥 **Downloading...**", sts, start)
        )
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await sts.edit("❌ FloodWait error. Try again later.")
    except Exception as e:
        return await sts.edit(f"❌ Download failed: `{e}`")

    await sts.edit("🔍 **Analyzing streams...**")

    info = get_media_info(dl)
    streams = list_streams(info)

    if not streams:
        os.remove(dl)
        return await sts.edit("❌ **No streams found in this file.**")

    text = "📋 **Streams Found:**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for s in streams:
        text += f"  {s}\n"
    text += (
        "\n\n**Reply with stream number to extract.**\n"
        "Example: `/extractstream 2` to extract stream #2"
    )

    await sts.edit(text)

    # Store file path for extraction
    from helper.auth import auth_users  # reuse dict for temp storage
    auth_users[f"extract_{user_id}"] = dl


@Client.on_message(
    (filters.private | filters.group) &
    filters.command("extractstream")
)
async def extract_stream_cmd(client, message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return await message.reply_text("❌ Only owner/admin can use this.")

    from helper.auth import auth_users
    dl_path = auth_users.get(f"extract_{user_id}")
    if not dl_path or not os.path.exists(dl_path):
        return await message.reply_text("❌ No file loaded. Use `/extract` first (reply to a video).")

    args = message.text.split()[1:]
    if not args:
        return await message.reply_text("❌ Provide stream index: `/extractstream <index>`")

    try:
        stream_idx = int(args[0])
    except ValueError:
        return await message.reply_text("❌ Invalid stream index.")

    info = get_media_info(dl_path)
    if not info or "streams" not in info:
        return await message.reply_text("❌ Cannot read file streams.")

    target_stream = None
    for s in info["streams"]:
        if s.get("index") == stream_idx:
            target_stream = s
            break

    if not target_stream:
        return await message.reply_text(f"❌ Stream #{stream_idx} not found.")

    codec_type = target_stream.get("codec_type", "")
    lang = target_stream.get("tags", {}).get("language", "und")

    if codec_type == "subtitle":
        ext = "srt"
    elif codec_type == "audio":
        codec = target_stream.get("codec_name", "aac")
        ext = {"aac": "aac", "opus": "ogg", "flac": "flac", "mp3": "mp3"}.get(codec, "mka")
    else:
        return await message.reply_text("❌ Can only extract audio or subtitle streams.")

    output_name = f"stream_{stream_idx}_{lang}.{ext}"
    output_path = f"downloads/{user_id}/{output_name}"

    sts = await message.reply_text(f"⏳ **Extracting stream #{stream_idx}...**")

    success = await extract_stream(dl_path, output_path, stream_idx, codec_type)

    if not success or not os.path.exists(output_path):
        return await sts.edit("❌ **Extraction failed.**")

    await sts.edit("📤 **Uploading extracted stream...**")

    try:
        # Build caption with metadata and replacor
        caption = f"📝 **Extracted:** Stream #{stream_idx} [{lang}]"

        # Apply metadata if enabled
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

        await message.reply_document(
            document=output_path,
            caption=caption,
        )
        await sts.delete()
    except Exception as e:
        await sts.edit(f"❌ Upload failed: `{e}`")
    finally:
        try:
            os.remove(output_path)
        except Exception:
            pass
