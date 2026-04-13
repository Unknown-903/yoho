from bot.helper.media_helper.auth import auth_chats
from bot.core.config_manager import Config


def is_owner(user_id):
    return user_id == Config.OWNER_ID


def is_admin(user_id):
    return user_id == Config.OWNER_ID or user_id in Config.ADMIN


async def is_premium(user_id):
    """Check if user has premium access."""
    from bot.helper.media_helper.database import codeflixbots
    return await codeflixbots.has_premium(user_id)


async def can_access_premium_feature(user_id):
    """Check if user can access premium features (admin OR premium)."""
    if is_admin(user_id):
        return True
    return await is_premium(user_id)


def is_authorized_chat(chat_id):
    return chat_id in auth_chats


async def check_permission(message, require_owner=False, require_admin=False, require_premium=False, require_auth=False):
    """
    Returns True agar permission hai, False agar nahi.
    Automatically error message bhi bhejta hai.
    """
    user_id = message.from_user.id if message.from_user else None
    chat_id = message.chat.id
    chat_type = message.chat.type

    # Group auth check
    if require_auth and chat_type in ["group", "supergroup"]:
        if chat_id not in auth_chats:
            await message.reply_text("❌ This group is not authorized.\nAsk admin to use /auth.")
            return False

    if user_id is None:
        await message.reply_text("❌ Anonymous users not allowed.")
        return False

    if require_owner and not is_owner(user_id):
        await message.reply_text("❌ Only owner can use this command.")
        return False

    if require_admin and not is_admin(user_id):
        await message.reply_text("❌ Only owner/admin can use this command.")
        return False

    if require_premium and not await can_access_premium_feature(user_id):
        from bot.helper.media_helper.database import codeflixbots
        contact = Config.ADMIN_URL or "the bot owner"
        await message.reply_text(
            "❌ **Premium Feature**\n\n"
            "This feature is only available for:\n"
            "✅ Admin/Owner\n"
            "✅ Premium Members\n\n"
            f"Contact {contact} to get premium access!"
        )
        return False

    return True
