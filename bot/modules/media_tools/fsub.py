"""
Force Subscribe channel management.
Owner can add/remove channels that users must join before using the bot.
"""
from bot.modules.media_tools.client_compat import Client
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from bot.core.config_manager import Config
from bot.helper.media_helper.database import codeflixbots


@Client.on_message(filters.command("addfsub") & filters.user(Config.OWNER_ID))
async def add_fsub_cmd(client, message):
    args = message.text.split()[1:]
    if not args:
        return await message.reply_text(
            "📋 **Force Subscribe Usage**\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "`/addfsub <channel_id>` → Add channel\n"
            "`/rmfsub <channel_id>` → Remove channel\n"
            "`/fsublist` → Show all channels\n\n"
            "**Note:** Bot must be admin in the channel."
        )

    try:
        channel_id = int(args[0])
    except ValueError:
        return await message.reply_text("❌ **Invalid Channel ID.**")

    try:
        chat = await client.get_chat(channel_id)
        title = chat.title or "Unknown"
        username = chat.username or ""
        invite_link = chat.invite_link or ""
        if not invite_link and username:
            invite_link = f"https://t.me/{username}"
        elif not invite_link:
            try:
                invite_link = await client.export_chat_invite_link(channel_id)
            except Exception:
                invite_link = ""
    except Exception as e:
        return await message.reply_text(f"❌ **Failed to get channel info:** `{e}`")

    await codeflixbots.add_fsub_channel(channel_id, title, invite_link, username)
    await message.reply_text(
        f"✅ **FSub Channel Added!**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📢 **Title:** {title}\n"
        f"🆔 **ID:** `{channel_id}`\n"
        f"🔗 **Link:** {invite_link or 'N/A'}"
    )


@Client.on_message(filters.command("rmfsub") & filters.user(Config.OWNER_ID))
async def remove_fsub_cmd(client, message):
    args = message.text.split()[1:]
    if not args:
        return await message.reply_text("❌ `/rmfsub <channel_id>`")
    try:
        channel_id = int(args[0])
    except ValueError:
        return await message.reply_text("❌ **Invalid Channel ID.**")

    await codeflixbots.remove_fsub_channel(channel_id)
    await message.reply_text(
        f"❌ **FSub Channel Removed**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 `{channel_id}`"
    )


@Client.on_message(filters.command("fsublist") & filters.user(Config.OWNER_ID))
async def fsub_list_cmd(client, message):
    channels = await codeflixbots.get_fsub_channels()
    if not channels:
        return await message.reply_text("📋 **No FSub channels configured.**")

    text = "📢 **Force Subscribe Channels**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, ch in enumerate(channels, 1):
        title = ch.get("title", "Unknown")
        cid = ch.get("channel_id")
        text += f"  {i}. **{title}** — `{cid}`\n"

    await message.reply_text(text)


# ================= FSub callback for "Try Again" button =================

@Client.on_callback_query(filters.regex("^check_fsub$"))
async def check_fsub_callback(client, callback_query: CallbackQuery):
    await callback_query.answer("♻️ Checking again...", show_alert=False)
    await callback_query.message.delete()
