"""
⚡ Leaderboard — Thunder theme with visual progress bars.
"""
from bot.modules.media_tools.client_compat import Client
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait
from bot.helper.media_helper.database import codeflixbots
from bot.core.config_manager import Config
import asyncio

CATEGORIES = {
    "all": {"emoji": "🏆", "label": "Overall"},
    "rename": {"emoji": "✏️", "label": "Rename"},
    "encode": {"emoji": "🎬", "label": "Encode"},
    "compress": {"emoji": "🗜️", "label": "Compress"},
    "merge": {"emoji": "🔀", "label": "Merge"},
    "upscale": {"emoji": "🔍", "label": "Upscale"},
}

MEDALS = ["🥇", "🥈", "🥉"]
RANK_BARS = ["⚡", "🔥", "✨", "💫", "⭐"]  # visual intensity


def _thunder_bar(count, max_count, width=12):
    """Build a thunder-themed progress bar."""
    if max_count <= 0:
        return "░" * width
    ratio = min(count / max_count, 1.0)
    filled = int(ratio * width)
    empty = width - filled
    return "▰" * filled + "▱" * empty


def _build_leaderboard_text(top_users, category="all", caller_rank=None):
    cat = CATEGORIES.get(category, CATEGORIES["all"])
    
    text = f"""<b>
╔══════════════════════════════╗
  {cat['emoji']} {cat['label'].upper()} LEADERBOARD {cat['emoji']}
╚══════════════════════════════╝
</b>"""

    if not top_users:
        text += "\n📭 <i>No activity yet! Be the first!</i>\n"
    else:
        max_count = 0
        for user in top_users:
            if category == "all":
                c = user.get("total_tasks", 0)
            else:
                c = user.get("task_counts", {}).get(category, 0)
            if c > max_count:
                max_count = c

        for i, user in enumerate(top_users):
            uid = user["_id"]
            username = user.get("username", None)
            
            if category == "all":
                count = user.get("total_tasks", 0)
            else:
                count = user.get("task_counts", {}).get(category, 0)
            
            medal = MEDALS[i] if i < 3 else f"<b>{i+1}.</b>"
            name = f"@{username}" if username else f"<code>{uid}</code>"
            bar = _thunder_bar(count, max_count)
            
            # Top 3 get special styling
            if i == 0:
                text += f"\n{medal} <b>{name}</b>\n"
                text += f"   ⚡ <code>{bar}</code> <b>{count}</b> tasks\n"
            elif i == 1:
                text += f"\n{medal} <b>{name}</b>\n"
                text += f"   🔥 <code>{bar}</code> <b>{count}</b> tasks\n"
            elif i == 2:
                text += f"\n{medal} <b>{name}</b>\n"
                text += f"   ✨ <code>{bar}</code> <b>{count}</b> tasks\n"
            else:
                text += f"\n{medal} {name}\n"
                text += f"   <code>{bar}</code> {count} tasks\n"

    text += "\n<b>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</b>"
    
    if caller_rank:
        text += f"\n📍 <b>Your Rank:</b> #{caller_rank}"
    
    text += "\n⚡ <i>Keep processing to climb!</i>"
    
    return text


def _build_category_buttons(current="all", user_id=None):
    uid = user_id or 0
    row1 = []
    row2 = []
    
    for key, cat in CATEGORIES.items():
        if key == current:
            label = f"» {cat['emoji']} {cat['label']} «"
        else:
            label = f"{cat['emoji']} {cat['label']}"
        
        btn = InlineKeyboardButton(label, callback_data=f"lb|{key}|{uid}")
        
        if key in ["all", "rename", "encode"]:
            row1.append(btn)
        else:
            row2.append(btn)
    
    return InlineKeyboardMarkup([
        row1, row2,
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"lb|{current}|{uid}"),
            InlineKeyboardButton("❌ Close", callback_data="lb_close")
        ]
    ])


@Client.on_message(filters.command(["leaderboard", "top", "lb"]))
async def leaderboard_cmd(client, message):
    user_id = message.from_user.id
    args = message.text.split()
    category = args[1].lower() if len(args) > 1 and args[1].lower() in CATEGORIES else "all"

    if category == "all":
        top_users = await codeflixbots.get_leaderboard(limit=15)
    else:
        top_users = await codeflixbots.get_leaderboard(limit=15, task_type=category)

    caller_rank = await codeflixbots.get_user_rank(user_id)
    text = _build_leaderboard_text(top_users, category, caller_rank)
    buttons = _build_category_buttons(category, user_id)
    
    if Config.LEADERBOARD_PIC:
        await message.reply_photo(photo=Config.LEADERBOARD_PIC, caption=text, reply_markup=buttons)
    else:
        await message.reply_text(text, reply_markup=buttons)


@Client.on_callback_query(filters.regex(r"^lb\|"))
async def leaderboard_callback(client, query: CallbackQuery):
    parts = query.data.split("|")
    if len(parts) != 3:
        return await query.answer("Invalid", show_alert=True)
    
    _, category, owner_id = parts
    caller_id = query.from_user.id
    
    if category == "all":
        top_users = await codeflixbots.get_leaderboard(limit=10)
    else:
        top_users = await codeflixbots.get_leaderboard(limit=10, task_type=category)
    
    caller_rank = await codeflixbots.get_user_rank(caller_id)
    text = _build_leaderboard_text(top_users, category, caller_rank)
    buttons = _build_category_buttons(category, caller_id)
    
    try:
        await query.message.edit_text(text, reply_markup=buttons)
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except:
        pass
    await query.answer(f"{CATEGORIES.get(category,{}).get('emoji','🏆')} Refreshed!")


@Client.on_callback_query(filters.regex(r"^lb_close$"))
async def lb_close(client, query: CallbackQuery):
    try:
        if Config.LEADERBOARD_DELETE_TIMER and Config.LEADERBOARD_DELETE_TIMER > 0:
            await query.message.delete()
        else:
            await query.message.delete()
    except:
        pass
