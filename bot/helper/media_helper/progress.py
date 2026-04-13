import asyncio
import time
import re
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait
import logging

logger = logging.getLogger(__name__)


def format_bytes(bytes_val):
    """Convert bytes to human readable format."""
    if bytes_val is None:
        return "?"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024:
            return f"{bytes_val:.1f}{unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f}PB"


def format_time(seconds):
    """Convert seconds to readable time format."""
    if seconds is None:
        return "?"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    seconds = seconds % 60
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h {minutes}m {seconds}s"


def parse_ffmpeg_progress(line):
    """Parse ffmpeg progress line (key=value format)."""
    progress = {}
    for item in line.split():
        if "=" in item:
            key, val = item.split("=", 1)
            progress[key] = val
    return progress


def calculate_progress(out_time_us, duration_us):
    """Calculate progress percentage from ffmpeg output."""
    if duration_us and duration_us > 0:
        return min(int((out_time_us / duration_us) * 100), 99)
    return 0


def calculate_eta(progress, elapsed_time):
    """Calculate ETA based on progress and elapsed time."""
    if progress <= 0:
        return None
    total_time = (elapsed_time / progress) * 100
    eta = total_time - elapsed_time
    return eta if eta > 0 else None


def calculate_speed(processed_bytes, elapsed_time):
    """Calculate speed in MB/s."""
    if elapsed_time <= 0:
        return 0
    return (processed_bytes / (1024 * 1024)) / elapsed_time


def format_progress_bar(progress, width=10):
    """Aeon-style progress bar: ●●●●●○○○○○"""
    p = min(max(progress, 0), 100)
    c_full = int((p + 5) // 10)
    return "●" * c_full + "○" * (width - c_full)


class ProgressTracker:
    """Track progress for encode/compress operations with detailed stats."""

    def __init__(self, task_id, filename, total_duration_sec=None, total_size_bytes=None):
        self.task_id = task_id
        self.filename = filename
        self.start_time = time.time()
        self.last_update = 0
        self.progress = 0
        
        # FFmpeg specific
        self.duration_us = int(total_duration_sec * 1_000_000) if total_duration_sec else 0
        self.out_time_us = 0
        
        # File transfer specific
        self.total_size = total_size_bytes or 0
        self.processed = 0
        
        self._last_processed = 0
        self._last_time = self.start_time

    def update_ffmpeg(self, line):
        """Update from ffmpeg progress line."""
        prog = parse_ffmpeg_progress(line)
        if "out_time_us" in prog:
            try:
                self.out_time_us = int(prog["out_time_us"])
                self.progress = calculate_progress(self.out_time_us, self.duration_us)
            except:
                pass

    def update_file_transfer(self, current_bytes):
        """Update from file transfer."""
        self.processed = current_bytes
        if self.total_size > 0:
            self.progress = min(int((current_bytes / self.total_size) * 100), 99)

    def get_elapsed(self):
        """Get elapsed time in seconds."""
        return time.time() - self.start_time

    def get_eta(self):
        """Get estimated time remaining in seconds."""
        elapsed = self.get_elapsed()
        return calculate_eta(self.progress, elapsed)

    def get_speed(self):
        """Get average speed in MB/s for file transfers."""
        elapsed = self.get_elapsed()
        if elapsed <= 0 or self.processed <= 0:
            return 0
        return calculate_speed(self.processed, elapsed)

    def get_size_left(self):
        """Get remaining bytes."""
        if self.total_size > 0:
            return self.total_size - self.processed
        return 0

    def format_status(self, emoji="⚙️", title="Encoding", settings_line=""):
        """Aeon-style status card with progress details."""
        elapsed = self.get_elapsed()
        bar = format_progress_bar(self.progress)
        eta = self.get_eta()
        eta_str = format_time(eta) if eta and eta > 0 else "-"

        header = f"{title}"
        if settings_line:
            header += f" • {settings_line}"

        fname = self.filename
        if len(fname) > 45:
            fname = fname[:42] + "..."

        lines = [
            f"<b>{header}</b>",
            f"{bar} {self.progress}%",
        ]
        if self.total_size > 0:
            lines.append(f"<b>Processed:</b> {format_bytes(self.processed)}/{format_bytes(self.total_size)}")
        if self.processed > 0:
            speed = self.get_speed()
            lines.append(f"<b>Speed:</b> {speed:.2f}MB/s")
        lines.append(f"<b>Estimated:</b> {eta_str}")
        lines.append(f"<b>File:</b> {fname}")

        return "\n".join(lines)


class SafeProgressEditor:
    """Safely edit progress messages with FloodWait handling."""

    def __init__(self, msg, user_id, task_id, min_interval=2.0):
        self.msg = msg
        self.user_id = user_id
        self.task_id = task_id
        self.min_interval = min_interval
        self.last_edit_time = 0
        self.is_editing = False
        self.last_text = ""

    async def edit(self, text, buttons=None, force=False):
        """Edit message with smart throttling and error handling."""
        now = time.time()
        
        # Skip if same text and not forced
        if text == self.last_text and not force:
            return
        
        # Skip if too soon (unless forced)
        if now - self.last_edit_time < self.min_interval and not force:
            return
        
        if self.is_editing:
            return
        
        self.is_editing = True
        try:
            await self.msg.edit(text, parse_mode="html")
            self.last_edit_time = now
            self.last_text = text
        except FloodWait as e:
            self.min_interval = max(self.min_interval, e.value + 0.5)
            logger.warning(f"FloodWait {e.value}s - adjusted interval to {self.min_interval}s")
        except Exception as e:
            logger.debug(f"Progress edit error: {e}")
        finally:
            self.is_editing = False

    async def final_update(self, text, buttons=None):
        """Final update - always tries once more."""
        try:
            await self.msg.edit(text, parse_mode="html")
        except Exception as e:
            logger.debug(f"Final edit failed: {e}")


class FFmpegProgressMonitor:
    """Monitor subprocess stdout for ffmpeg progress."""

    def __init__(self, process, tracker, duration_seconds):
        self.process = process
        self.tracker = tracker
        self.duration_seconds = duration_seconds
        self.task = None

    async def run(self):
        """Read stdout and update tracker."""
        try:
            while True:
                line = await asyncio.wait_for(
                    self.process.stdout.readline(),
                    timeout=60
                )
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text.startswith("out_time_us="):
                    self.tracker.update_ffmpeg(text)
        except asyncio.TimeoutError:
            logger.warning("FFmpeg progress timeout")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Progress monitor error: {e}")

    def start(self):
        """Start monitoring in background."""
        self.task = asyncio.create_task(self.run())

    def stop(self):
        """Stop monitoring."""
        if self.task and not self.task.done():
            self.task.cancel()


async def format_download_progress(current, total, start_time, filename):
    """Format download progress with speed and ETA."""
    now = time.time()
    elapsed = max(now - start_time, 0.1)
    
    if total > 0:
        progress = min(int((current / total) * 100), 100)
        bar = format_progress_bar(progress)
        
        speed = calculate_speed(current, elapsed)
        
        remaining = total - current
        if speed > 0:
            eta = remaining / (speed * 1024 * 1024)
        else:
            eta = None
        
        # Truncate filename
        fname = filename
        if len(fname) > 35:
            fname = fname[:32] + "..."
        
        eta_str = format_time(eta) if eta else "-"
        text = (
            f"<b>Download</b>\n"
            f"{bar} {progress}%\n"
            f"<b>Processed:</b> {format_bytes(current)}/{format_bytes(total)}\n"
            f"<b>Speed:</b> {speed:.2f}MB/s\n"
            f"<b>Estimated:</b> {eta_str}\n"
            f"<b>File:</b> {fname}"
        )
        
        return text, progress
    
    return "📥 Downloading", 0


async def format_upload_progress(current, total, start_time, filename):
    """Format upload progress with speed and ETA."""
    now = time.time()
    elapsed = max(now - start_time, 0.1)
    
    if total > 0:
        progress = min(int((current / total) * 100), 100)
        bar = format_progress_bar(progress)
        
        speed = calculate_speed(current, elapsed)
        
        remaining = total - current
        if speed > 0:
            eta = remaining / (speed * 1024 * 1024)
        else:
            eta = None
        
        # Truncate filename
        fname = filename
        if len(fname) > 35:
            fname = fname[:32] + "..."
        
        eta_str = format_time(eta) if eta else "-"
        text = (
            f"<b>Upload</b>\n"
            f"{bar} {progress}%\n"
            f"<b>Processed:</b> {format_bytes(current)}/{format_bytes(total)}\n"
            f"<b>Speed:</b> {speed:.2f}MB/s\n"
            f"<b>Estimated:</b> {eta_str}\n"
            f"<b>File:</b> {fname}"
        )
        
        return text, progress
    
    return "📤 Uploading", 0
