"""
Smart Audio Reorder Utility with Priority Matching.

Features:
  - Skip button for quick default order
  - Full track titles in buttons (codec + channels)
  - Move-up UX (swap with track above)
  - SMART PRIORITY MATCHING for batch: 
    User sets order [Hindi, English, Tamil]
    File has [English, Tamil] → auto-matches to [English, Tamil]
    File has [Tamil, Hindi] → auto-matches to [Hindi, Tamil]
    No matching tracks → skip reorder
  - Metadata + watermark + replacor support

Usage:
    from bot.helper.media_helper.audio_reorder import probe_and_reorder_audio, smart_reorder_by_priority
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

# Global state
_reorder_events = {}
_reorder_data = {}
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


def _get_audio_streams_fast(file_path, read_bytes=2*1024*1024):
    """Fast probe: read only first 2MB of file for quick track detection.
    Perfect for long videos where full probe would be slow.
    Falls back to full probe if fast probe fails.
    """
    try:
        # Fast probe with analyzeduration limit
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-analyzeduration", "2000000",  # 2 seconds max
             "-probesize", str(read_bytes),   # read limited bytes
             "-select_streams", "a",
             "-show_entries", "stream=index,codec_name,channels:stream_tags=language,title",
             "-of", "json", file_path],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if streams:
            return streams
    except Exception:
        pass
    # Fallback to full probe
    return _get_audio_streams(file_path)


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


def _get_track_lang_key(s):
    """Get normalized language key for matching."""
    tags = s.get("tags", {})
    lang = tags.get("language", "und").lower().strip()
    title = (tags.get("title", "") or "").lower().strip()
    label = get_language_label(lang).lower()
    return lang, title, label


def smart_reorder_by_priority(streams, priority_order):
    """
    Smart priority-based reordering for batch processing.
    
    priority_order: list of language labels user wants, e.g. ["Hindi", "English", "Tamil"]
    streams: list of audio stream dicts from ffprobe
    
    Algorithm:
    1. For each track in priority_order, find matching stream by language/title
    2. Matched tracks go first in priority order
    3. Unmatched tracks from file go after (preserving original order)
    4. If NO priority tracks match at all → return None (skip reorder)
    5. If at least 1 matches → build smart order
    
    Returns: new order list (indices into streams) or None if no matches
    """
    if not streams or not priority_order:
        return list(range(len(streams)))
    
    # Normalize priority list
    priority_normalized = [p.lower().strip() for p in priority_order]
    
    # Build mapping: for each stream, check if it matches any priority
    matched = []  # (priority_rank, stream_index)
    unmatched = []  # stream_index
    
    for idx, s in enumerate(streams):
        lang, title, label = _get_track_lang_key(s)
        
        best_rank = None
        for rank, prio in enumerate(priority_normalized):
            # Match by: exact lang code, label name, or title contains
            if (prio == lang or 
                prio == label or 
                prio in label or 
                label in prio or
                (title and prio in title) or
                (title and any(p in title for p in [prio]))):
                if best_rank is None or rank < best_rank:
                    best_rank = rank
        
        if best_rank is not None:
            matched.append((best_rank, idx))
        else:
            unmatched.append(idx)
    
    if not matched:
        return None  # No matches at all → skip
    
    # Sort matched by priority rank
    matched.sort(key=lambda x: x[0])
    
    # Build final order: matched first (in priority), then unmatched (original order)
    order = [idx for _, idx in matched] + unmatched
    return order


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
        if len(label) > 55:
            label = label[:52] + "..."
        if i == 0:
            btn_text = f"🔊 {label}"
        else:
            btn_text = f"⬆️ {i+1}. {label}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"areorder|{key}|{idx}")])
    buttons.append([
        InlineKeyboardButton("⏭️ Skip", callback_data=f"areorder_skip|{key}"),
        InlineKeyboardButton("✅ Done", callback_data=f"areorder_done|{key}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"areorder_cancel|{key}|{user_id}"),
    ])
    return InlineKeyboardMarkup(buttons)


def build_audio_map_args(streams, order):
    """Convert reorder result into ffmpeg -map arguments."""
    args = []
    for idx in order:
        if idx < len(streams):
            original_index = streams[idx]["index"]
            args += ["-map", f"0:{original_index}"]
    return args


async def probe_and_reorder_audio(client, file_path, user_id, task_id, progress_msg, timeout=300):
    """
    Probe audio streams (fast probe for long videos).
    If >1 stream, show reorder UI and wait.
    Returns (streams, order) tuple. order=None means cancelled.
    """
    # Use fast probe for quick detection
    streams = _get_audio_streams_fast(file_path)
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
    
    try:
        await asyncio.wait_for(_reorder_events[key].wait(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            await progress_msg.edit("⏰ Audio reorder timed out. Using default order.")
        except Exception:
            pass
    
    final_order = _reorder_data.get(key, {}).get("order", order)
    cancelled = _reorder_cancelled.get(key, False)
    
    _reorder_data.pop(key, None)
    _reorder_events.pop(key, None)
    _reorder_cancelled.pop(key, None)
    
    if cancelled:
        return streams, None
    return streams, final_order


def handle_reorder_done(key):
    event = _reorder_events.get(key)
    if event:
        event.set()

def handle_reorder_cancel(key):
    _reorder_cancelled[key] = True
    event = _reorder_events.get(key)
    if event:
        event.set()

def handle_reorder_skip(key):
    data = _reorder_data.get(key)
    if data:
        data["order"] = list(range(len(data["streams"])))
    event = _reorder_events.get(key)
    if event:
        event.set()


# ================= PYROGRAM CALLBACK HANDLERS =================

from pyrogram import Client, filters

@Client.on_callback_query(filters.regex(r"^areorder\|"))
async def _areorder_move_cb(client, query):
    """Move tapped track UP by one position."""
    parts = query.data.split("|")
    key = parts[1]
    stream_idx = int(parts[2])
    data = _reorder_data.get(key)
    if not data:
        return await query.answer("Session expired.", show_alert=True)
    order = data["order"]
    streams = data["streams"]
    user_id = data.get("user_id", 0)
    
    if stream_idx in order:
        pos = order.index(stream_idx)
        if pos == 0:
            await query.answer("🔊 Already #1 (default)")
            return
        order[pos], order[pos - 1] = order[pos - 1], order[pos]
    
    text = _build_text(streams, order)
    buttons = _build_buttons(streams, order, key, user_id)
    try:
        await query.message.edit_text(text, reply_markup=buttons)
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
