from bot.modules.media_tools.client_compat import Client
from pyrogram import filters, ContinuePropagation
from plugins.file_rename import file_queue, process_queue, extract_season_episode
from bot.helper.media_helper.auth import auth_chats
from bot.helper.media_helper.permissions import is_authorized_chat
from bot.core.config_manager import Config

sequence_sessions = {}

# ================= ADMIN CHECK =================

def _is_admin_seq(user_id):
    return user_id == Config.OWNER_ID or user_id in Config.ADMIN


# ================= START SEQUENCE =================

@Client.on_message((filters.private | filters.group) & filters.command("sequence"))
async def start_sequence(client, message):

    user_id = message.from_user.id

    # admin check
    if not _is_admin_seq(user_id):
        return

    # group auth check
    if message.chat.type in ["group","supergroup"]:
        if not is_authorized_chat(message.chat.id):
            return await message.reply_text("❌ Yeh group authorized nahi hai.")

    sequence_sessions[user_id] = []

    await message.reply_text(
        "📂 Sequence Mode Enabled\n\nSend files now.\nSend /done when finished."
    )


# ================= CAPTURE FILES =================

@Client.on_message(
    (filters.private | filters.group)
    & (filters.document | filters.video | filters.audio),
    group=2
)
async def collect_files(client, message):

    user_id = message.from_user.id if message.from_user else None

    if not user_id or user_id not in sequence_sessions:
        raise ContinuePropagation

    file = message.document or message.video or message.audio

    file_name = file.file_name or ""

    season, episode = extract_season_episode(file_name)

    season = int(season or 0)
    episode = int(episode or 0)

    sequence_sessions[user_id].append({
        "season": season,
        "episode": episode,
        "message": message
    })

    await message.reply_text(
        f"📥 Added → S{season:02d}E{episode:02d}"
    )


# ================= DONE =================

@Client.on_message((filters.private | filters.group) & filters.command("done"))
async def finish_sequence(client, message):

    user_id = message.from_user.id

    if not _is_admin_seq(user_id):
        return

    if user_id not in sequence_sessions:
        return await message.reply_text("⚠️ No active sequence")

    files = sequence_sessions[user_id]

    if not files:
        return await message.reply_text("⚠️ No files received")

    # sort files
    files.sort(key=lambda x: (x["season"], x["episode"]))

    for data in files:
        await file_queue.put((client, data["message"]))

    del sequence_sessions[user_id]

    await message.reply_text("✅ Files sorted and added to queue")

    import asyncio
    asyncio.create_task(process_queue())
