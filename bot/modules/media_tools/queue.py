"""Show all active media processing tasks."""
from bot.modules.media_tools.client_compat import Client
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.helper.media_helper.task_manager import task_manager
from bot.helper.media_helper.permissions import is_admin
from bot.core.telegram_manager import TgClient


def _bar(p, w=10):
    c = int((min(max(p,0),100) + 5) // 10)
    return "●" * c + "○" * (w - c)


def _fe(s):
    s = int(s)
    if s < 60: return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60: return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


async def queue_cmd(client, message):
    task_manager.cleanup_stale(hrs=6)
    tasks = task_manager.get_all_active()
    uid = message.from_user.id

    if not tasks:
        return await message.reply_text("<b>No active media tasks.</b>", parse_mode="html")

    lines = [f"<b>🎬 Active Tasks: {len(tasks)}</b>\n"]
    for i, t in enumerate(tasks, 1):
        fname = t["file_name"]
        if len(fname) > 45: fname = fname[:42] + "..."
        prog = t["progress"]
        status = t["status"]
        elapsed = _fe(int(time.time() - t["started"]))

        cmd_emoji = {
            "encode":"🎬","compress":"🗜","merge":"🔗",
            "rename":"✏️","upscale":"🔍","autorename":"🔄","extract":"📦"
        }.get(t["command"], "⚙️")

        lines.append(f"<b>{i}. {cmd_emoji} /{t['command']}</b>")
        lines.append(f"<code>{fname}</code>")
        if prog > 0:
            lines.append(f"{_bar(prog)} {prog}%")
        lines.append(f"<b>Status:</b> {status.capitalize()} | <b>Elapsed:</b> {elapsed}")
        uname = t.get("username", "")
        lines.append(f"<b>User:</b> {'@'+uname if uname else t['user_id']}")
        lines.append(f"/cancel {t['task_id']}\n")

    text = "\n".join(lines)
    await message.reply_text(text, parse_mode="html")


import time
