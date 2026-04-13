from bot.core.config_manager import Config
import importlib
_start_cb = importlib.import_module("plugins.start_cb")
Txt = _start_cb.Txt
from bot.helper.media_helper.database import codeflixbots
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from bot.modules.media_tools.client_compat import Client
from pyrogram import filters
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked, PeerIdInvalid

import os
import sys
import time
import asyncio
import logging
import datetime

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ADMIN_USER_ID = Config.ADMIN


# ================= RESTART =================

@Client.on_message(filters.command("restart") & filters.user(Config.OWNER_ID))
async def restart_bot_owner(bot, message):
    import datetime
    import pytz

    # Get restart details
    ist = pytz.timezone('Asia/Kolkata')
    restart_time = datetime.datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S IST')
    restarted_by = message.from_user.mention if message.from_user.username else f"User {message.from_user.id}"
    bot_username = (await bot.get_me()).username or "Unknown"

    # Send notification to support chat
    try:
        await bot.send_message(
            Config.SUPPORT_CHAT,
            f"🔄 **Bot Restarted Successfully!**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 **Date & Time:** `{restart_time}`\n"
            f"👤 **Restarted By:** {restarted_by}\n"
            f"🤖 **Bot Username:** @{bot_username}\n\n"
            f"✅ **Status:** Online and ready!\n"
            f"📦 **Repository:** [GitHub](https://github.com/sujiop56/Auto-Everything)"
        )
    except Exception as e:
        print(f"Failed to send restart notification: {e}")

    await message.reply_text("♻️ **Restarting bot...**")

    os.execv(sys.executable, ['python'] + sys.argv)


# ================= TUTORIAL =================

@Client.on_message(filters.private & filters.command("tutorial"))
async def tutorial(bot: Client, message: Message):

    user_id = message.from_user.id
    format_template = await codeflixbots.get_format_template(user_id)

    await message.reply_text(
        text=Txt.FILE_NAME_TXT.format(format_template=format_template),
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("• ᴏᴡɴᴇʀ", url="https://t.me/cosmic_freak"),
                InlineKeyboardButton("• ᴛᴜᴛᴏʀɪᴀʟ", url="https://t.me/codeflix_bots")
            ]
        ])
    )


# ================= BOT STATS =================

@Client.on_message(filters.command(["stats", "status"]) & filters.user(Config.ADMIN))
async def get_stats(bot, message):

    total_users = await codeflixbots.total_users_count()

    uptime = time.strftime(
        "%Hh%Mm%Ss",
        time.gmtime(time.time() - bot.uptime)
    )

    start_t = time.time()
    st = await message.reply("**Accessing Bot Details...**")
    end_t = time.time()

    ping = (end_t - start_t) * 1000

    await st.edit(
        f"**-- Bot Status --**\n\n"
        f"**⌚ Uptime :** {uptime}\n"
        f"**⚡ Ping :** `{ping:.3f} ms`\n"
        f"**👥 Users :** `{total_users}`"
    )


# ================= BROADCAST =================

@Client.on_message(filters.command("broadcast") & filters.user(Config.ADMIN) & filters.reply)
async def broadcast_handler(bot: Client, m: Message):

    await bot.send_message(
        Config.LOG_CHANNEL,
        f"{m.from_user.mention} started broadcast"
    )

    all_users = await codeflixbots.get_all_users()
    broadcast_msg = m.reply_to_message

    sts_msg = await m.reply_text("📡 **Broadcast Started...**")

    done = 0
    success = 0
    failed = 0

    start_time = time.time()
    total_users = await codeflixbots.total_users_count()

    async for user in all_users:

        sts = await send_msg(user["_id"], broadcast_msg)

        if sts == 200:
            success += 1
        else:
            failed += 1

        if sts == 400:
            await codeflixbots.delete_user(user["_id"])

        done += 1

        if not done % 20:
            await sts_msg.edit(
                f"📡 **Broadcast Progress**\n\n"
                f"Users : {total_users}\n"
                f"Completed : {done}/{total_users}\n"
                f"Success : {success}\n"
                f"Failed : {failed}"
            )

    completed_in = datetime.timedelta(
        seconds=int(time.time() - start_time)
    )

    await sts_msg.edit(
        f"✅ **Broadcast Completed**\n\n"
        f"Time : `{completed_in}`\n\n"
        f"Users : {total_users}\n"
        f"Completed : {done}\n"
        f"Success : {success}\n"
        f"Failed : {failed}"
    )


# ================= SEND MESSAGE =================

async def send_msg(user_id, message):

    try:
        await message.copy(chat_id=int(user_id))
        return 200

    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await send_msg(user_id, message)

    except InputUserDeactivated:
        logger.info(f"{user_id} : Deactivated")
        return 400

    except UserIsBlocked:
        logger.info(f"{user_id} : Blocked")
        return 400

    except PeerIdInvalid:
        logger.info(f"{user_id} : Invalid ID")
        return 400

    except Exception as e:
        logger.error(f"{user_id} : {e}")
        return 500
