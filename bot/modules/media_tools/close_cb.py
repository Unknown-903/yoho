"""Universal close button handler."""
from bot.modules.media_tools.client_compat import Client
from pyrogram import filters
from pyrogram.types import CallbackQuery

@Client.on_callback_query(filters.regex(r"^close$"))
async def close_cb(client, query: CallbackQuery):
    try: await query.message.delete()
    except: pass
