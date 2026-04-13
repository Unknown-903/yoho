"""
Reusable decorators for ban check, FSub enforcement, and premium gating.
Adapted from Auto-Rename (abhinai2244) — integrated into si-main architecture.
"""
import asyncio
import logging
from functools import wraps
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant
from bot.core.config_manager import Config
from bot.helper.media_helper.database import codeflixbots

logger = logging.getLogger(__name__)

ADMIN_URL = Config.ADMIN_URL
OWNER_ID  = Config.OWNER_ID


# ================= BAN CHECK DECORATOR =================

def check_ban(func):
    """Block banned users from using the bot."""
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        user_id = message.from_user.id
        if user_id == OWNER_ID or user_id in Config.ADMIN:
            return await func(client, message, *args, **kwargs)
        user = await codeflixbots.col.find_one({"_id": user_id})
        if user and user.get("ban_status", {}).get("is_banned", False):
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("📩 Contact Admin", url=ADMIN_URL)]]
            )
            return await message.reply_text(
                "❌ **You are banned from using this bot.**\n"
                "If you think this is a mistake, contact admin.",
                reply_markup=keyboard,
            )
        return await func(client, message, *args, **kwargs)
    return wrapper


# ================= FORCE SUBSCRIBE DECORATOR =================

def check_fsub(func):
    """Enforce channel subscription before allowing bot usage."""
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        user_id = message.from_user.id
        if user_id == OWNER_ID or user_id in Config.ADMIN:
            return await func(client, message, *args, **kwargs)

        channels = await codeflixbots.get_fsub_channels()
        if not channels:
            return await func(client, message, *args, **kwargs)

        not_joined = []
        for ch in channels:
            try:
                member = await client.get_chat_member(ch["channel_id"], user_id)
                if member.status not in {
                    ChatMemberStatus.OWNER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.MEMBER,
                }:
                    not_joined.append(ch)
            except UserNotParticipant:
                not_joined.append(ch)
            except Exception:
                pass

        if not_joined:
            buttons = []
            for ch in not_joined:
                invite = ch.get("invite_link", f"https://t.me/{ch.get('username', '')}")
                buttons.append(
                    [InlineKeyboardButton(f"📢 Join {ch.get('title', 'Channel')}", url=invite)]
                )
            buttons.append([InlineKeyboardButton("♻️ Try Again", callback_data="check_fsub")])
            return await message.reply_text(
                "⚠️ **Please join the required channels first!**",
                reply_markup=InlineKeyboardMarkup(buttons),
            )

        return await func(client, message, *args, **kwargs)
    return wrapper


# ================= PREMIUM CHECK DECORATOR =================

def check_premium(func):
    """Gate features behind premium access."""
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        user_id = message.from_user.id
        if user_id == OWNER_ID or user_id in Config.ADMIN:
            return await func(client, message, *args, **kwargs)
        if await codeflixbots.has_premium(user_id):
            return await func(client, message, *args, **kwargs)
        return await message.reply_text(
            "🌟 **Premium Feature**\n\n"
            "This feature requires premium access.\n"
            "Contact the admin to get premium!",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📩 Get Premium", url=ADMIN_URL)]]
            ),
        )
    return wrapper
