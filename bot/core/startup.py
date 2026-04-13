from asyncio import sleep
from os import environ

import aiohttp
from aiofiles import open as aiopen
from aiofiles.os import makedirs, remove
from aiofiles.os import path as aiopath
from aioshutil import rmtree

from bot import (
    LOGGER,
    auth_chats,
    drives_ids,
    drives_names,
    excluded_extensions,
    included_extensions,
    index_urls,
    rss_dict,
    shorteners_list,
    sudo_users,
    user_data,
)
from bot.helper.ext_utils.db_handler import database
from .config_manager import Config
from .telegram_manager import TgClient


async def load_settings():
    """Load bot settings from database."""
    if not Config.DATABASE_URL:
        return

    config_dict = await database.get_bot_settings()
    if config_dict:
        Config.load_dict(config_dict)

    # Load user data
    users = await database.get_users_data()
    if users:
        user_data.update(users)

    # Load auth chats
    chats = await database.get_auth_chats()
    if chats:
        auth_chats.update(chats)

    # Load sudo users
    db_sudo = await database.get_sudo_users()
    if db_sudo:
        sudo_users.extend(db_sudo)

    # Load RSS
    rss = await database.get_rss_data()
    if rss:
        rss_dict.update(rss)

    LOGGER.info("Settings loaded from database")


async def load_configurations():
    """Load additional runtime configurations."""
    # Excluded extensions
    if Config.EXCLUDED_EXTENSIONS:
        excluded_extensions.extend(
            x.strip().lower().lstrip(".")
            for x in Config.EXCLUDED_EXTENSIONS.split(",")
            if x.strip()
        )

    # Included extensions
    if Config.INCLUDED_EXTENSIONS:
        included_extensions.extend(
            x.strip().lower().lstrip(".")
            for x in Config.INCLUDED_EXTENSIONS.split(",")
            if x.strip()
        )

    # Sudo users from config
    if Config.SUDO_USERS:
        for uid in Config.SUDO_USERS.split():
            try:
                sudo_users.append(int(uid))
            except ValueError:
                pass


async def save_settings():
    """Save current settings to database."""
    if not Config.DATABASE_URL:
        return
    await database.save_bot_settings(Config.get_all())


async def update_variables():
    """Update runtime variables from config."""
    pass


async def restart_notification():
    """Send restart notification if applicable."""
    if await aiopath.isfile(".restartmsg"):
        try:
            async with aiopen(".restartmsg") as f:
                chat_id, msg_id = map(int, (await f.read()).split())
            await TgClient.bot.edit_message_text(
                chat_id, msg_id, "Bot restarted successfully!"
            )
        except Exception:
            pass
        await remove(".restartmsg")
