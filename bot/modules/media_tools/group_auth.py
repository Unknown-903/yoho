import math
from bot.modules.media_tools.client_compat import Client
from pyrogram import filters
from helper.auth import (
    auth_chats, auth_users,
    add_auth_user, remove_auth_user, is_auth_user,
    get_auth_remaining, get_all_auth_users,
)
from bot.core.config_manager import Config


def is_owner(user_id):
    return user_id == Config.OWNER_ID


# ================= /auth COMMAND =================

@Client.on_message((filters.private | filters.group) & filters.command("auth"))
async def authorize_cmd(client, message):

    if not is_owner(message.from_user.id):
        return await message.reply_text("❌ Sirf owner use kar sakta hai")

    args = message.text.split()[1:]  # everything after /auth
    reply = message.reply_to_message

    target_id = None
    days = None

    # ── Via Reply ──
    if reply and reply.from_user:
        target_id = reply.from_user.id
        # /auth 4  (reply) → 4 days
        # /auth    (reply) → unlimited
        if args:
            try:
                days = int(args[0])
            except ValueError:
                return await message.reply_text(
                    "❌ **Invalid days**\n\n"
                    "**Usage (reply):**\n"
                    "`/auth` → unlimited\n"
                    "`/auth 4` → 4 days"
                )

    # ── Via User ID ──
    elif args:
        try:
            target_id = int(args[0])
        except ValueError:
            return await message.reply_text(
                "❌ **Invalid User ID**\n\n"
                "**Usage:**\n"
                "`/auth <user_id>` → unlimited\n"
                "`/auth <user_id> <days>` → X days\n"
                "Or reply to a user's message."
            )
        if len(args) >= 2:
            try:
                days = int(args[1])
            except ValueError:
                return await message.reply_text("❌ **Days must be a number**\nExample: `/auth 123456 7`")

    # ── No target ──
    else:
        # Fall back to authorizing current chat (group)
        chat_id = message.chat.id
        if message.chat.type in ["group", "supergroup"]:
            if chat_id in auth_chats:
                return await message.reply_text(
                    f"⚠️ **This group is already authorized**\n\n"
                    f"🆔 `{chat_id}`"
                )
            auth_chats.add(chat_id)
            return await message.reply_text(
                f"✅ **Group Authorized**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🆔 **Chat ID:** `{chat_id}`\n"
                f"⏳ **Duration:** `Unlimited`"
            )
        else:
            return await message.reply_text(
                "📋 **Auth Usage**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "**By Reply:**\n"
                "┊ `/auth` → unlimited\n"
                "┊ `/auth 4` → 4 days\n\n"
                "**By User ID:**\n"
                "┊ `/auth <id>` → unlimited\n"
                "┊ `/auth <id> <days>` → X days\n\n"
                "**In Group (no args):**\n"
                "┊ `/auth` → authorize this group"
            )

    # ── Authorize User ──
    if is_auth_user(target_id):
        remaining = get_auth_remaining(target_id)
        if remaining is None:
            status = "Unlimited"
        else:
            status = f"{math.ceil(remaining)} day(s)"
        return await message.reply_text(
            f"⚠️ **User Already Authorized**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **User ID:** `{target_id}`\n"
            f"⏳ **Remaining:** `{status}`\n\n"
            f"💡 Use `/rauth` to remove first, then re-auth."
        )

    add_auth_user(target_id, days)

    duration_text = f"`{days}` day(s)" if days else "`Unlimited`"

    await message.reply_text(
        f"✅ **User Authorized**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 **User ID:** `{target_id}`\n"
        f"⏳ **Duration:** {duration_text}"
    )


# ================= /rauth COMMAND =================

@Client.on_message((filters.private | filters.group) & filters.command("rauth"))
async def unauthorize_cmd(client, message):

    if not is_owner(message.from_user.id):
        return await message.reply_text("❌ Sirf owner use kar sakta hai")

    args = message.text.split()[1:]
    reply = message.reply_to_message

    target_id = None

    if reply and reply.from_user:
        target_id = reply.from_user.id
    elif args:
        try:
            target_id = int(args[0])
        except ValueError:
            return await message.reply_text("❌ **Invalid User ID**")
    else:
        # Remove group auth
        chat_id = message.chat.id
        if chat_id in auth_chats:
            auth_chats.discard(chat_id)
            return await message.reply_text(
                f"❌ **Group Unauthorized**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🆔 `{chat_id}`"
            )
        return await message.reply_text("⚠️ **This chat is not authorized**")

    if not is_auth_user(target_id):
        return await message.reply_text(
            f"⚠️ **User is not authorized**\n\n"
            f"👤 **User ID:** `{target_id}`"
        )

    remove_auth_user(target_id)
    await message.reply_text(
        f"❌ **User Unauthorized**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 **User ID:** `{target_id}`"
    )


# ================= /authlist COMMAND =================

@Client.on_message((filters.private | filters.group) & filters.command("authlist"))
async def auth_list(client, message):

    if not is_owner(message.from_user.id):
        return await message.reply_text("❌ Sirf owner use kar sakta hai")

    users = get_all_auth_users()
    has_chats = bool(auth_chats)
    has_users = bool(users)

    if not has_chats and not has_users:
        return await message.reply_text(
            "📋 **Auth List**\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "💤 _No authorized chats or users._"
        )

    text = "📋 **Auth List**\n━━━━━━━━━━━━━━━━━━━━\n\n"

    if has_chats:
        text += "🏠 **Authorized Groups:**\n"
        for cid in auth_chats:
            text += f"  ┊ `{cid}` — ♾ Unlimited\n"
        text += "\n"

    if has_users:
        text += "👤 **Authorized Users:**\n"
        for uid, expiry in users.items():
            if expiry is None:
                text += f"  ┊ `{uid}` — ♾ Unlimited\n"
            else:
                import time
                remaining = expiry - time.time()
                if remaining > 0:
                    d = math.ceil(remaining / 86400)
                    text += f"  ┊ `{uid}` — ⏳ {d} day(s) left\n"

    text += "\n━━━━━━━━━━━━━━━━━━━━"
    await message.reply_text(text)
