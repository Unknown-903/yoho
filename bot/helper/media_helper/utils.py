import time
import re
import asyncio
from datetime import datetime
from pytz import timezone
from bot.core.config_manager import Config
from pyrogram.errors import FloodWait

# ================= PROGRESS BAR =================

last_edit_times = {}  # message_id -> last edit timestamp

async def progress_for_pyrogram(current, total, ud_type, message, start, cancel_data=None):
    now = time.time()
    diff = now - start
    if diff <= 0:
        return

    # Throttle updates to avoid FloodWait
    msg_id = message.id
    last = last_edit_times.get(msg_id, 0)
    if now - last < 5 and current != total:
        return
    last_edit_times[msg_id] = now

    percentage = 0.0
    if total and total > 0:
        percentage = min(max(current * 100 / total, 0.0), 100.0)

    speed = current / diff if diff > 0 else 0
    eta_seconds = 0
    if total and total > current and speed > 0:
        eta_seconds = int((total - current) / speed)

    eta_text = TimeFormatter(eta_seconds * 1000) if eta_seconds else "-"

    # Aeon-style progress bar: ●●●●●○○○○○
    c_full = int((percentage + 5) // 10)
    p_str = "●" * c_full + "○" * (10 - c_full)

    text = (
        f"<b>{ud_type}</b>\n"
        f"{p_str} {round(percentage, 2)}%\n"
        f"<b>Processed:</b> {humanbytes(current)}/{humanbytes(total if total else 0)}\n"
        f"<b>Speed:</b> {humanbytes(speed)}/s\n"
        f"<b>Estimated:</b> {eta_text}"
    )

    try:
        await message.edit_text(text, parse_mode="html")
        if current == total:
            last_edit_times.pop(msg_id, None)
    except FloodWait as e:
        last_edit_times[msg_id] = time.time() + e.value
    except Exception:
        pass

# ================= HUMAN BYTES =================

def humanbytes(size):
    if not size or size == 0:
        return "0 B"
    if size < 0:
        return f"-{humanbytes(-size)}"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    power = 1024
    n = 0
    while size >= power and n < len(units) - 1:
        size /= power
        n += 1
    return f"{round(size, 2)} {units[n]}"

# ================= TIME FORMAT =================

def TimeFormatter(milliseconds: int) -> str:
    if milliseconds <= 0:
        return "0s"
    seconds, _ = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)

# ================= TIME CONVERTER =================

def convert(seconds):
    seconds = seconds % (24 * 3600)
    hour = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    return "%d:%02d:%02d" % (hour, minutes, seconds)

# ================= LOG NEW USER =================

async def send_log(b, u):
    if Config.LOG_CHANNEL is None:
        return
    curr = datetime.now(timezone("Asia/Kolkata"))
    date = curr.strftime("%d %B, %Y")
    time_ = curr.strftime("%I:%M:%S %p")
    try:
        await b.send_message(
            Config.LOG_CHANNEL,
            f"**━━ New User Started Bot ━━**\n\n"
            f"👤 **User:** {u.mention}\n"
            f"🆔 **ID:** `{u.id}`\n"
            f"📛 **Username:** @{u.username}\n\n"
            f"📅 **Date:** `{date}`\n"
            f"🕐 **Time:** `{time_}`\n\n"
            f"🤖 **By:** {b.mention}",
        )
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await send_log(b, u)
    except Exception:
        pass

# ================= METADATA HELPERS =================

async def build_metadata_args(user_id, original_title=""):
    from bot.helper.media_helper.database import codeflixbots
    from lang_map import compose_title_with_prefix

    metadata_args = []
    if not await codeflixbots.get_metadata(user_id):
        return metadata_args

    user_title = (await codeflixbots.get_title(user_id)) or ""
    title = compose_title_with_prefix(original_title or "", user_title)
    author = await codeflixbots.get_author(user_id) or ""
    artist = await codeflixbots.get_artist(user_id) or ""
    album = await codeflixbots.get_album(user_id) or ""
    genre = await codeflixbots.get_genre(user_id) or ""
    publisher = await codeflixbots.get_publisher(user_id) or ""
    encoded_by = await codeflixbots.get_encoded_by(user_id) or ""
    comment = await codeflixbots.get_comment(user_id) or ""
    channel = await codeflixbots.get_channel(user_id) or ""
    license_tag = await codeflixbots.get_license(user_id) or ""
    copyright_tag = await codeflixbots.get_copyright(user_id) or ""
    description = await codeflixbots.get_description(user_id) or ""
    encoder = await codeflixbots.get_metadata_code(user_id) or ""

    if title:
        metadata_args += ["-metadata", f"title={title}"]
    if author:
        metadata_args += ["-metadata", f"author={author}"]
    if artist:
        metadata_args += ["-metadata", f"artist={artist}"]
    if album:
        metadata_args += ["-metadata", f"album={album}"]
    if genre:
        metadata_args += ["-metadata", f"genre={genre}"]
    if publisher:
        metadata_args += ["-metadata", f"publisher={publisher}"]
    if encoded_by:
        metadata_args += ["-metadata", f"encoded_by={encoded_by}"]
    if comment:
        metadata_args += ["-metadata", f"comment={comment}"]
    if channel:
        metadata_args += ["-metadata", f"channel={channel}"]
    if license_tag:
        metadata_args += ["-metadata", f"license={license_tag}"]
    if copyright_tag:
        metadata_args += ["-metadata", f"copyright={copyright_tag}"]
    if description:
        metadata_args += ["-metadata", f"description={description}"]
    if encoder:
        metadata_args += ["-metadata", f"encoder={encoder}"]

    # Per-stream metadata: apply title to ALL video, audio, and subtitle tracks
    stream_title = title or user_title or ""
    if stream_title and metadata_args:
        for _idx in range(10):
            metadata_args += ["-metadata:s:v:" + str(_idx), f"title={stream_title}"]
            metadata_args += ["-metadata:s:a:" + str(_idx), f"title={stream_title}"]
            metadata_args += ["-metadata:s:s:" + str(_idx), f"title={stream_title}"]

    return metadata_args

# ================= WATERMARK CHECK HELPER =================

async def check_watermark_for_process(user_id, process_name, client=None, msg=None):
    """
    Check watermark settings for a specific process.
    Returns True if watermark should be applied, False otherwise.
    
    process_name: 'rename', 'encode', 'compress', 'merge', 'upscale', 'autorename'
    
    Modes: 'on' = always apply, 'off' = never, 'ask' = ask user
    """
    from bot.helper.media_helper.database import codeflixbots
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    # Check per-process watermark setting (defaults to global watermark_apply)
    wm_apply = await codeflixbots.get_watermark_apply(user_id)
    if not wm_apply or wm_apply == "off":
        return False
    
    # Check if watermark is even configured
    wm_text = await codeflixbots.get_watermark_text(user_id)
    wm_image = await codeflixbots.get_watermark_image(user_id)
    if not wm_text and not wm_image:
        return False

    if wm_apply == "on":
        return True

    if wm_apply == "ask" and client and msg:
        # Show inline ask prompt
        import asyncio
        _wm_events = {}
        _wm_results = {}
        
        key = f"wm_{user_id}_{process_name}"
        _wm_events[key] = asyncio.Event()
        _wm_results[key] = False
        
        preview = f"`{wm_text}`" if wm_text else "Image watermark"
        text = (
            f"💧 **Apply Watermark?**\n\n"
            f"**Process:** {process_name.title()}\n"
            f"**Watermark:** {preview}\n\n"
            f"⚠️ Applying watermark will re-encode the file (takes longer)."
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes", callback_data=f"wm_ask|{key}|yes"),
             InlineKeyboardButton("❌ No", callback_data=f"wm_ask|{key}|no")]
        ])
        try:
            await msg.edit(text, reply_markup=buttons)
            await asyncio.wait_for(_wm_events[key].wait(), timeout=60)
            return _wm_results.get(key, False)
        except Exception:
            return False
        finally:
            _wm_events.pop(key, None)
            _wm_results.pop(key, None)

    return False


# ================= PREFIX SUFFIX =================

def add_prefix_suffix(input_string, prefix="", suffix=""):
    pattern = r"(?P<filename>.*?)(\.\w+)?$"
    match = re.search(pattern, input_string)
    if not match:
        return input_string
    filename = match.group("filename")
    extension = match.group(2) or ""
    if prefix:
        filename = f"{prefix}{filename}"
    if suffix:
        filename = f"{filename} {suffix}"
    return f"{filename}{extension}"

def apply_replacor(filename, replacor_strings, replacor_final):
    """Apply replacor to a filename or text string.

    Replaces each string in replacor_strings with replacor_final.
    Longest matches are replaced first to avoid partial overlap issues.
    Case-insensitive replacement.
    """
    if not filename or not replacor_strings:
        return filename
    if replacor_final is None:
        return filename

    sorted_strings = sorted((s for s in replacor_strings if s), key=len, reverse=True)
    result = filename
    for s in sorted_strings:
        pattern = re.escape(s)
        result = re.sub(pattern, replacor_final, result, flags=re.IGNORECASE)
    return result

# ================= SAFE MESSAGE EDIT =================

async def safe_edit(message, text, reply_markup=None, parse_mode=None):
    """Edit message with FloodWait protection."""
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            pass
    except Exception:
        pass

async def safe_send(client, chat_id, text, **kwargs):
    """Send message with FloodWait protection."""
    try:
        return await client.send_message(chat_id, text, **kwargs)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await client.send_message(chat_id, text, **kwargs)
