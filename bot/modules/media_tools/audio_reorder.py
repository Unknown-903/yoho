"""
Shared audio reorder utility.
Import and use in encode.py, compress.py, merge.py, file_rename.py.

Usage:
    from helper.audio_reorder import probe_and_reorder_audio

    # In your process function, after downloading the file:
    audio_map, file_path = await probe_and_reorder_audio(
        client, file_path, user_id, task_id, progress_msg, timeout=300
    )
    # audio_map = list of original stream indices in user's chosen order
    # Use audio_map to build ffmpeg -map commands
"""
import os
import json
import asyncio
import subprocess
import logging

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait
from lang_map import get_language_label

logger = logging.getLogger(__name__)

# Global state for audio reorder across all plugins
_reorder_events = {}   # key -> asyncio.Event
_reorder_data = {}     # key -> {"streams": [...], "order": [...], "user_id": int}
_reorder_cancelled = {}


def _get_audio_streams(file_path):
    """Probe audio streams from file."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index,codec_name,channels:stream_tags=language,title",
             "-of", "json", file_path],
            capture_output=True, text=True, timeout=30
        )
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except Exception as e:
        logger.info(f"Audio probe failed: {e}")
        return []


def _track_label(s, include_codec=True):
    """Build full human-readable label for an audio track."""
    tags = s.get("tags", {})
    lang = get_language_label(tags.get("language", "und"))
    title = tags.get("title", "")
    codec = s.get("codec_name", "?").upper()
    ch = s.get("channels", "?")
    ch_label = f"{ch}ch" if ch != "?" else ""

    parts = []
    if title:
        parts.append(title)
    if lang and lang != title:
        parts.append(f"({lang})")
    if include_codec:
        codec_info = " | ".join(filter(None, [codec, ch_label]))
        if codec_info:
            parts.append(f"| {codec_info}")
    return " ".join(parts) or "Unknown Track"


def _build_text(streams, order):
    """Build numbered audio track display with full info."""
    text = "🎛 **Audio Track Order**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, idx in enumerate(order):
        s = streams[idx]
        label = _track_label(s)
        if i == 0:
            text += f"🔊 **DEFAULT** → {label}\n"
        else:
            text += f"  {i+1}. {label}\n"
    text += "\n━━━━━━━━━━━━━━━━━━━━\n"
    text += "⬆️ Tap track to move it up\n"
    text += "🔊 First track = default audio"
    return text


def _build_buttons(streams, order, key, user_id):
    """Build inline buttons with full track titles."""
    buttons = []
    for i, idx in enumerate(order):
        s = streams[idx]
        label = _track_label(s, include_codec=True)
        # Truncate if too long for Telegram (max ~64 chars for callback_data button text)
        if len(label) > 55:
            label = label[:52] + "..."
        # Show position marker + full label
        if i == 0:
            btn_text = f"🔊 {label}"
        else:
            btn_text = f"⬆️ {i+1}. {label}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"areorder|{key}|{idx}")])

    # Action row: Skip + Continue + Cancel
    buttons.append([
        InlineKeyboardButton("⏭️ Skip", callback_data=f"areorder_skip|{key}"),
        InlineKeyboardButton("✅ Done", callback_data=f"areorder_done|{key}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"areorder_cancel|{key}|{user_id}"),
    ])
    return InlineKeyboardMarkup(buttons)


def build_audio_map_args(streams, order):
    """Convert reorder result into ffmpeg -map arguments.
    Returns list like ["-map", "0:1", "-map", "0:2", "-map", "0:3"]
    """
    args = []
    for idx in order:
        if idx < len(streams):
            original_index = streams[idx]["index"]
            args += ["-map", f"0:{original_index}"]
    return args


async def probe_and_reorder_audio(client, file_path, user_id, task_id, progress_msg, timeout=300):
    """
    Probe audio streams. If >1, show reorder UI and wait.
    Returns (streams, order) tuple.
    - streams: list of stream dicts from ffprobe
    - order: list of indices in user's chosen order

    If only 1 or 0 streams, returns immediately with default order.
    """
    streams = _get_audio_streams(file_path)
    order = list(range(len(streams)))

    if len(streams) <= 1:
        return streams, order

    key = str(task_id)
    _reorder_data[key] = {"streams": streams, "order": order, "user_id": user_id}
    _reorder_events[key] = asyncio.Event()
    _reorder_cancelled[key] = False

    text = _build_text(streams, order)
    buttons = _build_buttons(streams, order, key, user_id)

    try:
        await progress_msg.edit(text, reply_markup=buttons)
    except Exception:
        pass

    # Wait for user action
    try:
        await asyncio.wait_for(_reorder_events[key].wait(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            await progress_msg.edit("⏰ Audio reorder timed out. Using default order.")
        except Exception:
            pass

    final_order = _reorder_data.get(key, {}).get("order", order)
    cancelled = _reorder_cancelled.get(key, False)

    # Cleanup
    _reorder_data.pop(key, None)
    _reorder_events.pop(key, None)
    _reorder_cancelled.pop(key, None)

    if cancelled:
        return streams, None  # None signals cancellation

    return streams, final_order


def handle_reorder_done(key):
    """Called by callback handler when user clicks Done."""
    event = _reorder_events.get(key)
    if event:
        event.set()


def handle_reorder_cancel(key):
    """Called by callback handler when user clicks Cancel."""
    _reorder_cancelled[key] = True
    event = _reorder_events.get(key)
    if event:
        event.set()


def handle_reorder_skip(key):
    """Called by callback handler when user clicks Skip (use default order)."""
    data = _reorder_data.get(key)
    if data:
        # Reset to original order (0, 1, 2, ...)
        data["order"] = list(range(len(data["streams"])))
    event = _reorder_events.get(key)
    if event:
        event.set()


# ================= PYROGRAM CALLBACK HANDLERS =================
# These must be registered by importing this module in a plugin file
# that uses @Client.on_callback_query

from bot.modules.media_tools.client_compat import Client
from pyrogram import filters

@Client.on_callback_query(filters.regex(r"^areorder\|"))
async def _areorder_move_cb(client, query):
    """Move tapped track UP by one position (swap with track above)."""
    parts = query.data.split("|")
    key = parts[1]
    stream_idx = int(parts[2])
    data = _reorder_data.get(key)
    if not data:
        return await query.answer("Session expired.", show_alert=True)
    order = data["order"]
    streams = data["streams"]
    user_id = data.get("user_id", 0)

    # Find current position and move up
    if stream_idx in order:
        pos = order.index(stream_idx)
        if pos == 0:
            # Already at top — wrap to bottom or just acknowledge
            await query.answer("🔊 Already #1 (default)")
            return
        # Swap with track above
        order[pos], order[pos - 1] = order[pos - 1], order[pos]

    text = _build_text(streams, order)
    buttons = _build_buttons(streams, order, key, user_id)
    try:
        await query.message.edit_text(text, reply_markup=buttons)
        # Show which track moved
        moved_label = _track_label(streams[stream_idx], include_codec=False)
        new_pos = order.index(stream_idx) + 1
        if new_pos == 1:
            await query.answer(f"🔊 {moved_label} → #1 (default)")
        else:
            await query.answer(f"⬆️ {moved_label} → #{new_pos}")
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^areorder_done\|"))
async def _areorder_done_cb(client, query):
    key = query.data.split("|")[1]
    handle_reorder_done(key)
    await query.answer("✅ Continuing with selected order...")


@Client.on_callback_query(filters.regex(r"^areorder_skip\|"))
async def _areorder_skip_cb(client, query):
    key = query.data.split("|")[1]
    handle_reorder_skip(key)
    await query.answer("⏭️ Skipping — using default order")


@Client.on_callback_query(filters.regex(r"^areorder_cancel\|"))
async def _areorder_cancel_cb(client, query):
    parts = query.data.split("|")
    key = parts[1]
    handle_reorder_cancel(key)
    await query.answer("❌ Cancelled")
