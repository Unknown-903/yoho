"""
Audio Rearrangement Plugin (/af)
Interactively reorder audio streams in a video file.
Set default audio track by moving it to the top.
"""
import os
import json
import time
import asyncio
import subprocess
import logging
import sys

from bot.modules.media_tools.client_compat import Client
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait

from bot.helper.media_helper.utils import progress_for_pyrogram, apply_replacor, build_metadata_args
from lang_map import get_original_title
from bot.helper.media_helper.database import codeflixbots
from lang_map import get_language_label
from bot.helper.media_helper.permissions import is_admin as _perm_is_admin, is_authorized_chat
from bot.core.config_manager import Config

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

af_state = {}  # user_id -> {msg, streams, order, progress_msg}


def get_audio_streams(file_path):
    """Probe audio streams from video file."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index,codec_name,channels,sample_rate,bit_rate:stream_tags=language,title",
             "-of", "json", file_path],
            capture_output=True, text=True, timeout=30
        )
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except Exception as e:
        logger.error(f"ffprobe error: {e}")
        return []


def build_stream_list_text(streams, order):
    """Build display text for current audio stream order."""
    text = "🎛 **Audio Stream Order**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, idx in enumerate(order):
        stream = streams[idx]
        tags = stream.get("tags", {})
        lang = get_language_label(tags.get("language", "und"))
        title = tags.get("title", "")
        codec = stream.get("codec_name", "?")
        channels = stream.get("channels", "?")

        prefix = "🔊 **DEFAULT** → " if i == 0 else f"  {i+1}. "
        label = f"{title} ({lang})" if title else f"{lang}"
        text += f"{prefix}{label} | {codec} | {channels}ch\n"

    text += "\n━━━━━━━━━━━━━━━━━━━━\n"
    text += "💡 Tap a track to move it to **#1 (default)**\n"
    text += "✅ Press **Done** when finished"
    return text


def build_stream_buttons(streams, order, user_id):
    """Build inline buttons for each audio stream."""
    buttons = []
    for i, idx in enumerate(order):
        stream = streams[idx]
        tags = stream.get("tags", {})
        lang = get_language_label(tags.get("language", "und"))
        title = tags.get("title", "")
        codec = stream.get("codec_name", "?")
        label = f"{title} ({lang})" if title else f"{lang} | {codec}"
        if i == 0:
            label = f"🔊 {label} (DEFAULT)"
        buttons.append([InlineKeyboardButton(label, callback_data=f"af_move|{idx}|{user_id}")])

    buttons.append([
        InlineKeyboardButton("✅ Done — Remux", callback_data=f"af_done|{user_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"af_cancel|{user_id}"),
    ])
    return InlineKeyboardMarkup(buttons)


@Client.on_message((filters.private | filters.group) & filters.command("af") & filters.reply)
async def af_cmd(client, message):
    user_id = message.from_user.id
    if not _perm_is_admin(user_id):
        return await message.reply_text("❌ Only owner/admin can use this.")

    reply = message.reply_to_message
    if not (reply.video or reply.document):
        return await message.reply_text("❌ Reply to a video/document file.")

    progress_msg = await message.reply_text("📥 Downloading for audio analysis...")

    file_path = await client.download_media(reply, file_name=f"af_{user_id}_{int(time.time())}.mkv")
    if not file_path:
        return await progress_msg.edit("❌ Download failed.")

    streams = get_audio_streams(file_path)
    if len(streams) < 2:
        os.remove(file_path)
        return await progress_msg.edit("ℹ️ Only 1 audio stream found — nothing to reorder.")

    order = list(range(len(streams)))

    af_state[user_id] = {
        "msg": reply,
        "file_path": file_path,
        "streams": streams,
        "order": order,
    }

    text = build_stream_list_text(streams, order)
    buttons = build_stream_buttons(streams, order, user_id)
    await progress_msg.edit(text, reply_markup=buttons)


@Client.on_callback_query(filters.regex(r"^af_move\|"))
async def af_move_cb(client, query: CallbackQuery):
    parts = query.data.split("|")
    stream_idx = int(parts[1])
    owner_id = int(parts[2])
    caller_id = query.from_user.id

    if caller_id != owner_id and caller_id != Config.OWNER_ID:
        return await query.answer("❌ Not your session!", show_alert=True)

    state = af_state.get(caller_id) or af_state.get(owner_id)
    if not state:
        return await query.answer("Session expired.", show_alert=True)

    uid = owner_id if owner_id in af_state else caller_id
    order = state["order"]
    streams = state["streams"]

    # Move selected stream to position 0 (default)
    if stream_idx in order:
        order.remove(stream_idx)
        order.insert(0, stream_idx)

    text = build_stream_list_text(streams, order)
    buttons = build_stream_buttons(streams, order, uid)

    try:
        await query.message.edit_text(text, reply_markup=buttons)
        await query.answer(f"🔊 Moved to #1")
    except:
        pass


@Client.on_callback_query(filters.regex(r"^af_done\|"))
async def af_done_cb(client, query: CallbackQuery):
    owner_id = int(query.data.split("|")[1])
    caller_id = query.from_user.id

    if caller_id != owner_id and caller_id != Config.OWNER_ID:
        return await query.answer("❌ Not your session!", show_alert=True)

    uid = owner_id if owner_id in af_state else caller_id
    state = af_state.pop(uid, None)
    if not state:
        return await query.answer("Session expired.", show_alert=True)

    file_path = state["file_path"]
    streams = state["streams"]
    order = state["order"]
    msg = state["msg"]

    await query.message.edit_text("⚙️ Remuxing with new audio order...")

    # Build ffmpeg command to remap audio streams
    output = f"af_out_{uid}_{int(time.time())}.mkv"

    cmd = ["ffmpeg", "-i", file_path, "-map", "0:v"]
    for idx in order:
        original_index = streams[idx]["index"]
        cmd += ["-map", f"0:{original_index}"]
    cmd += ["-map", "0:s?", "-c", "copy"]

    # Apply metadata if enabled
    try:
        metadata_args = await build_metadata_args(uid, original_title=get_original_title(file_path))
        if metadata_args:
            cmd += metadata_args
    except Exception:
        pass  # Skip metadata on error

    cmd += ["-y", output]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

        if not os.path.exists(output) or os.path.getsize(output) == 0:
            await query.message.edit_text(f"❌ Remux failed!\n`{stderr.decode()[:200]}`")
            return
    except asyncio.TimeoutError:
        await query.message.edit_text("❌ Remux timed out.")
        return
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

    # Upload
    await query.message.edit_text("📤 Uploading...")
    try:
        # Get original filename
        if msg.document and msg.document.file_name:
            name = msg.document.file_name
        elif msg.video and msg.video.file_name:
            name = msg.video.file_name
        else:
            name = os.path.basename(output)

        final_name = name
        os.rename(output, final_name)

        # Build caption with metadata and replacor
        caption = f"🎛 **Audio Reordered**\n📁 `{final_name}`"

        # Apply metadata if enabled
        if await codeflixbots.get_metadata(uid):
            user_title = await codeflixbots.get_title(uid) or ""
            author = await codeflixbots.get_author(uid) or ""
            artist = await codeflixbots.get_artist(uid) or ""
            metadata_code = await codeflixbots.get_metadata_code(uid) or ""
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
        r_enabled = await codeflixbots.get_replacor_enabled(uid)
        if r_enabled:
            r_strings = await codeflixbots.get_replacor_strings(uid)
            r_final = await codeflixbots.get_replacor_final(uid)
            if r_strings and r_final:
                caption = apply_replacor(caption, r_strings, r_final)

        await client.send_document(
            chat_id=uid,
            document=final_name,
            caption=caption,
        )
        await query.message.delete()
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception as e:
        await query.message.edit_text(f"❌ Upload failed: {e}")
    finally:
        for f in [output, final_name if 'final_name' in dir() else None]:
            try:
                if f and os.path.exists(f):
                    os.remove(f)
            except:
                pass


@Client.on_callback_query(filters.regex(r"^af_cancel\|"))
async def af_cancel_cb(client, query: CallbackQuery):
    owner_id = int(query.data.split("|")[1])
    uid = owner_id if owner_id in af_state else query.from_user.id
    state = af_state.pop(uid, None)
    if state and os.path.exists(state.get("file_path", "")):
        os.remove(state["file_path"])
    await query.message.edit_text("❌ Audio rearrangement cancelled.")
