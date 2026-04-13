"""Cancel a media processing task by ID."""
from bot.modules.media_tools.client_compat import Client
from pyrogram import filters
from bot.helper.media_helper.permissions import is_admin
from bot.helper.media_helper.task_manager import task_manager


async def cancel_task_cmd(client, message):
    uid = message.from_user.id
    if not is_admin(uid):
        return await message.reply_text("❌ Only admins can cancel tasks.")

    args = message.text.split()
    if len(args) < 2:
        return await message.reply_text(
            "⚠️ Usage: <code>/cancel &lt;task_id&gt;</code>\n\nUse /queue to see task IDs.",
            parse_mode="html"
        )

    try:
        task_id = int(args[1])
    except ValueError:
        return await message.reply_text("❌ Invalid task ID.")

    cancelled = False

    # Try each plugin's cancel dict
    try:
        from bot.modules.media_tools.encode import cancel_tasks as enc_cancel, active_tasks as enc_active
        if task_id in enc_active or task_id in enc_cancel:
            enc_cancel[task_id] = True
            cancelled = True
    except Exception:
        pass

    try:
        from bot.modules.media_tools.compress import cancel_tasks as cmp_cancel, active_tasks as cmp_active
        if task_id in cmp_active or task_id in cmp_cancel:
            cmp_cancel[task_id] = True
            cancelled = True
    except Exception:
        pass

    try:
        from bot.modules.media_tools.file_rename import cancel_tasks as ren_cancel
        if task_id in ren_cancel:
            ren_cancel[task_id] = True
            cancelled = True
    except Exception:
        pass

    try:
        from bot.modules.media_tools.upscale import cancel_upscale
        if task_id in cancel_upscale:
            cancel_upscale[task_id] = True
            cancelled = True
    except Exception:
        pass

    try:
        from bot.modules.media_tools.merge import active_tasks as mrg_active, cancel_tasks as mrg_cancel
        if task_id in mrg_active or task_id in mrg_cancel:
            mrg_cancel[task_id] = True
            cancelled = True
    except Exception:
        pass

    task_manager.complete(task_id)

    if cancelled:
        await message.reply_text(
            f"✅ <b>Cancel signal sent for task</b> <code>{task_id}</code>\n"
            "Task will stop at next checkpoint.",
            parse_mode="html"
        )
    else:
        await message.reply_text(
            f"❌ <b>No active task with ID</b> <code>{task_id}</code>\n"
            "Use /queue to see current task IDs.",
            parse_mode="html"
        )
