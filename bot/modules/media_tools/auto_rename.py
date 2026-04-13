from bot.modules.media_tools.client_compat import Client
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait
from bot.helper.media_helper.database import codeflixbots
from bot.helper.media_helper.auth import auth_chats
from bot.helper.media_helper.permissions import is_authorized_chat
import asyncio


# ================= AUTORENAME =================

@Client.on_message((filters.private | filters.group) & filters.command("autorename"))
async def auto_rename_command(client, message):

    # Group authorization check
    if message.chat.type in ["group", "supergroup"]:
        if not is_authorized_chat(message.chat.id):
            return await message.reply_text(
                "❌ **This group is not authorized.**\n"
                "Use `/auth` first."
            )

    user_id = message.from_user.id

    # Extract command argument
    command_parts = message.text.split(maxsplit=1)

    if len(command_parts) < 2 or not command_parts[1].strip():
        return await message.reply_text(
            "**✏️ A U T O  R E N A M E**\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ **Please provide a rename format.**\n\n"
            "💡 **Example:**\n"
            "`/autorename Overflow [S{season}E{episode}] [{quality}] [{source}] {audio}`\n\n"
            "📝 **Available Placeholders:**\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            "  `{season}`   → Season number (`01`)\n"
            "  `{episode}`  → Episode number (`05`)\n"
            "  `{quality}`  → Quality (`1080p`, `720p`)\n"
            "  `{vcodec}`   → Video codec (`HEVC`, `x264`, `AV1`)\n"
            "  `{acodec}`   → Audio codec (`AAC`, `DD+`, `FLAC`)\n"
            "  `{audio}`    → Smart audio (`Hin Eng AAC 2.0`)\n"
            "  `{languages}` → All langs (`Hin Eng Tam`)\n"
            "  `{year}`     → Year (`2024`)\n"
            "  `{bitdepth}` → Bit depth (`10bit`, `8bit`)\n"
            "  `{title}`    → Auto-detected title\n"
            "  `{hdr}`      → HDR type (`HDR10`, `DV`)\n"
            "  `{group}`    → Release group\n"
            "  `{channels}` → Channels (`2.0`, `5.1`)\n"
            "  `{source}`   → Source (`WEB-DL`, `BluRay`, `AMZN`)\n"
            "  `{filename}` → Original filename (with extension)\n"
            "  `{filename_no_ext}` → Original filename (without extension)\n"
            "  `{vcodec}`  → Video codec\n"
            "  `{acodec}`  → Audio codec\n"
            "  `{subs}`    → Subtitle type (`MSubs`, `ESubs`, `SingleSubs`)\n\n"
            "🔍 **Auto-detected patterns:**\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            "  `S01E01` · `S1E5` · `S01-E05`\n"
            "  `Season 1 Episode 5` · `1x05`\n"
            "  `Ep05` · `Episode 3` · `E05`\n"
            "  `- 03` (anime) · `#05` · `Chapter 5`\n"
            "  `Part 2` · `Vol 3` · `OVA 01`\n"
            "  `001v2` (versioned)\n\n"
            "💡 **Tip:** Use Settings → Replacor to auto-replace\n"
            "unwanted tags from filenames!\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )

    format_template = command_parts[1].strip()

    # Save template
    await codeflixbots.set_format_template(user_id, format_template)

    await message.reply_text(
        "✅ **Rename Template Saved!**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 **Template:**\n`{format_template}`\n\n"
        "📦 Now send files to rename.\n"
        "Use `/select 1-12` to set episode range."
    )
