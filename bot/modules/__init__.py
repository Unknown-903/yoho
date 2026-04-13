# ruff: noqa: F401, F403
"""
Import all modules. Media tools self-register via client_compat shim.
Aeon modules are registered explicitly in handlers.py.
"""

# ── Aeon core modules ─────────────────────────────────────────────────────────
from .status import task_status, status_pages
from .cancel_task import cancel, cancel_multi, cancel_all_buttons, cancel_all_update
from .rss import get_rss_menu, rss_listener
from .clone import clone_node
from .search import torrent_search, torrent_search_update, initiate_search_tools
from .restart import restart_bot, confirm_restart
from .bot_settings import send_bot_settings, edit_bot_settings, aeon_callback
from .users_settings import send_user_settings, edit_user_settings, get_users_settings
from .broadcast import broadcast
from .chat_permission import authorize, unauthorize, add_sudo, remove_sudo
from .exec import aioexecute, execute, clear
from .mediainfo import mediainfo
from .speedtest import speedtest
from .stats import bot_stats, ping
from .shell import run_shell
from .help import bot_help, arg_usage, start
from .file_selector import select, confirm_selection
from .force_start import remove_from_queue
from .sox import spectrum_handler

# ── Leech ─────────────────────────────────────────────────────────────────────
from .mirror_leech import leech

# ── Media tools (import triggers @Client.on_* registration via client_compat) ─
from .media_tools import encode as _mt_encode
from .media_tools import compress as _mt_compress
from .media_tools import merge as _mt_merge
from .media_tools import file_rename as _mt_rename
from .media_tools import upscale as _mt_upscale
from .media_tools import auto_rename as _mt_autorename
from .media_tools import extract as _mt_extract
from .media_tools import settings as _mt_settings
from .media_tools import admin_panel as _mt_admin
from .media_tools import fsub as _mt_fsub
from .media_tools import group_auth as _mt_groupauth
from .media_tools import close_cb as _mt_close
from .media_tools import leaderboard as _mt_leaderboard
from .media_tools import premium as _mt_premium
from .media_tools import sequence as _mt_sequence
from .media_tools import audio_rearrange as _mt_audio_rearrange
from .media_tools import audio_reorder as _mt_audio_reorder


async def get_packages_version():
    pass


async def restart_notification():
    from aiofiles.os import path as aiopath
    from aiofiles import open as aiopen
    from bot.core.telegram_manager import TgClient
    if await aiopath.isfile(".restartmsg"):
        try:
            async with aiopen(".restartmsg") as f:
                chat_id, msg_id = map(int, (await f.read()).split())
            await TgClient.bot.edit_message_text(chat_id, msg_id, "✅ Bot restarted!")
        except Exception:
            pass
        from aiofiles.os import remove
        await remove(".restartmsg")
