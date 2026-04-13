# ruff: noqa: E402, PLC0415
from asyncio import gather
from pyrogram.types import BotCommand

from . import LOGGER, bot_loop
from .core.config_manager import Config, SystemEnv

LOGGER.info("Loading config...")
Config.load()
SystemEnv.load()

from .core.startup import load_settings
bot_loop.run_until_complete(load_settings())

from .core.telegram_manager import TgClient
from .helper.telegram_helper.bot_commands import BotCommands

COMMANDS = {
    "LeechCommand":       "- Leech file from Telegram",
    "CloneCommand":       "- Clone file on Google Drive",
    "SearchCommand":      "- Search for torrents",
    "StatusCommand":      "- Show active tasks",
    "StatsCommand":       "- Show bot & system stats",
    "CancelAllCommand":   "- Cancel all your tasks",
    "HelpCommand":        "- Get detailed help",
    "SpeedTest":          "- Run a speedtest",
    "UserSetCommand":     "- User settings",
    "BotSetCommand":      "- [ADMIN] Bot settings",
    "LogCommand":         "- [ADMIN] View log",
    "RestartCommand":     "- [ADMIN] Restart bot",
    # Media tools
    "EncodeCommand":      "- Encode video (H.265/H.264)",
    "CompressCommand":    "- Compress video file",
    "MergeCommand":       "- Merge multiple videos",
    "RenameCommand":      "- Rename a file",
    "UpscaleCommand":     "- AI upscale image/video",
    "AutoRenameCommand":  "- Auto rename files",
    "ExtractCommand":     "- Extract audio/subtitles",
    "QueueCommand":       "- Show processing queue",
}

COMMAND_OBJECTS = []
for cmd, description in COMMANDS.items():
    val = getattr(BotCommands, cmd, None)
    if val is None:
        continue
    name = val[0] if isinstance(val, list) else val
    COMMAND_OBJECTS.append(BotCommand(name, description))


async def set_commands():
    if Config.SET_COMMANDS:
        await TgClient.bot.set_bot_commands(COMMAND_OBJECTS)


async def main():
    from .core.startup import (
        load_configurations,
        save_settings,
        update_variables,
        restart_notification,
    )
    from .helper.ext_utils.files_utils import clean_all
    from .helper.ext_utils.telegraph_helper import telegraph
    from .modules import get_packages_version, initiate_search_tools

    await gather(TgClient.start_bot(), TgClient.start_user())
    await gather(load_configurations(), update_variables())

    await gather(
        set_commands(),
        save_settings(),
        clean_all(),
        initiate_search_tools(),
        get_packages_version(),
        restart_notification(),
        telegraph.create_account(),
    )
    LOGGER.info("Bot initialized successfully")


bot_loop.run_until_complete(main())

from .core.handlers import add_handlers
# Import all media_tools modules to trigger @Client.on_* decorator registration
from . import modules  # noqa: F401
from .helper.ext_utils.bot_utils import create_help_buttons

create_help_buttons()
add_handlers()

LOGGER.info("Bot Started!")
bot_loop.run_forever()
