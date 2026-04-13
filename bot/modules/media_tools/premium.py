"""
Premium/VIP user management system.
Owner can grant/revoke premium access with optional expiry.
"""
import math
from bot.modules.media_tools.client_compat import Client
from pyrogram import filters
from bot.core.config_manager import Config
from bot.helper.media_helper.database import codeflixbots


@Client.on_message(filters.command("addpremium") & filters.user(Config.OWNER_ID))
async def add_premium_cmd(client, message):
    args = message.text.split()[1:]
    reply = message.reply_to_message

    target_id = None
    days = None

    if reply and reply.from_user:
        target_id = reply.from_user.id
        if args:
            try:
                days = int(args[0])
            except ValueError:
                return await message.reply_text("❌ **Days must be a number.**")
    elif args:
        try:
            target_id = int(args[0])
        except ValueError:
            return await message.reply_text("❌ **Invalid User ID.**")
        if len(args) >= 2:
            try:
                days = int(args[1])
            except ValueError:
                return await message.reply_text("❌ **Days must be a number.**")
    else:
        return await message.reply_text(
            "📋 **Premium Usage**\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "**By Reply:**\n"
            "┊ `/addpremium` → unlimited\n"
            "┊ `/addpremium 30` → 30 days\n\n"
            "**By User ID:**\n"
            "┊ `/addpremium <id>` → unlimited\n"
            "┊ `/addpremium <id> <days>` → X days"
        )

    await codeflixbots.add_premium(target_id, days)
    duration = f"`{days}` day(s)" if days else "`Unlimited`"
    await message.reply_text(
        f"🌟 **Premium Granted!**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 **User ID:** `{target_id}`\n"
        f"⏳ **Duration:** {duration}"
    )


@Client.on_message(filters.command("rmpremium") & filters.user(Config.OWNER_ID))
async def remove_premium_cmd(client, message):
    args = message.text.split()[1:]
    reply = message.reply_to_message

    target_id = None
    if reply and reply.from_user:
        target_id = reply.from_user.id
    elif args:
        try:
            target_id = int(args[0])
        except ValueError:
            return await message.reply_text("❌ **Invalid User ID.**")
    else:
        return await message.reply_text("❌ Reply to a user or provide ID.\n`/rmpremium <user_id>`")

    await codeflixbots.remove_premium(target_id)
    await message.reply_text(
        f"❌ **Premium Removed**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 **User ID:** `{target_id}`"
    )


@Client.on_message(filters.command("premiumlist") & filters.user(Config.OWNER_ID))
async def premium_list_cmd(client, message):
    users = await codeflixbots.get_all_premium()
    if not users:
        return await message.reply_text("📋 **No premium users found.**")

    text = "🌟 **Premium Users**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, u in enumerate(users, 1):
        uid = u["user_id"]
        expiry = u.get("expiry")
        if expiry is None:
            status = "Unlimited"
        else:
            import datetime
            remaining = (expiry - datetime.datetime.utcnow()).total_seconds()
            if remaining > 0:
                status = f"{math.ceil(remaining / 86400)} day(s)"
            else:
                status = "Expired"
        text += f"  {i}. `{uid}` — {status}\n"

    await message.reply_text(text)


@Client.on_message(filters.command("mypremium"))
async def my_premium_cmd(client, message):
    user_id = message.from_user.id
    remaining = await codeflixbots.get_premium_remaining(user_id)
    if remaining == -1:
        return await message.reply_text("❌ **You don't have premium access.**")
    if remaining is None:
        status = "♾ Unlimited"
    else:
        status = f"📅 {math.ceil(remaining)} day(s) remaining"
    await message.reply_text(
        f"🌟 **Your Premium Status**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ **Status:** {status}"
    )
