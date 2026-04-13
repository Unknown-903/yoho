"""Leech module - Telegram file download and upload."""
from asyncio import create_task

from bot import LOGGER, DOWNLOAD_DIR, task_dict_lock
from bot.core.telegram_manager import TgClient
from bot.helper.aeon_utils.access_check import error_check
from bot.helper.ext_utils.bot_utils import new_task, arg_parser
from bot.helper.listeners.task_listener import TaskListener
from bot.helper.mirror_leech_utils.download_utils.telegram_download import (
    TelegramDownloadHelper,
)
from bot.helper.telegram_helper.message_utils import (
    auto_delete_message,
    delete_links,
    send_message,
)


class Leech(TaskListener):
    def __init__(self, client, message, same_dir=None, bulk=None,
                 multi_tag=None, options=""):
        if same_dir is None:
            same_dir = {}
        if bulk is None:
            bulk = []
        self.message = message
        self.client = client
        self.multi_tag = multi_tag
        self.options = options
        self.same_dir = same_dir
        self.bulk = bulk
        super().__init__()
        self.is_leech = True

    async def new_event(self):
        error_msg, error_button = await error_check(self.message)
        if error_msg:
            await delete_links(self.message)
            error = await send_message(self.message, error_msg, error_button)
            return await auto_delete_message(error, time=300)

        # Must be reply to a file
        reply = self.message.reply_to_message
        if not reply:
            return await send_message(
                self.message,
                "Reply to a file/media to leech it!"
            )

        media = (
            reply.document or reply.video or reply.audio or
            reply.photo or reply.voice or reply.video_note or
            reply.animation or None
        )
        if not media:
            return await send_message(
                self.message,
                "No downloadable media found in replied message!"
            )

        # Set up listener name from media
        if hasattr(media, "file_name") and media.file_name:
            self.name = media.file_name
        else:
            self.name = f"leech_{self.mid}"

        self.size = getattr(media, "file_size", 0)

        path = f"{DOWNLOAD_DIR}{self.mid}/"
        tg = TelegramDownloadHelper(self)
        await tg.add_download(reply, path, TgClient.bot)


@new_task
async def leech(client, message):
    Leech(client, message).new_event()
