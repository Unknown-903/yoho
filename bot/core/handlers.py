# ruff: noqa: F405
from pyrogram.filters import command, regex
from pyrogram.handlers import CallbackQueryHandler, EditedMessageHandler, MessageHandler

from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.filters import CustomFilters
from bot.modules import *

from .telegram_manager import TgClient


def add_handlers():
    # Register all media_tools plugin handlers (they used @Client.on_message decorator)
    from bot.modules.media_tools.client_compat import register_all_handlers
    register_all_handlers()

    command_filters = {
        # ── Auth / Admin ───────────────────────────────────────────────────
        "authorize":         (authorize,          BotCommands.AuthorizeCommand,    CustomFilters.sudo),
        "unauthorize":       (unauthorize,         BotCommands.UnAuthorizeCommand,  CustomFilters.sudo),
        "add_sudo":          (add_sudo,            BotCommands.AddSudoCommand,      CustomFilters.sudo),
        "remove_sudo":       (remove_sudo,         BotCommands.RmSudoCommand,       CustomFilters.sudo),
        "send_bot_settings": (send_bot_settings,   BotCommands.BotSetCommand,       CustomFilters.sudo),
        "broadcast":         (broadcast,           BotCommands.BroadcastCommand,    CustomFilters.owner),
        "run_shell":         (run_shell,            BotCommands.ShellCommand,        CustomFilters.owner),
        "aioexecute":        (aioexecute,           BotCommands.AExecCommand,        CustomFilters.sudo),
        "execute":           (execute,              BotCommands.ExecCommand,          CustomFilters.sudo),
        "clear":             (clear,                BotCommands.ClearLocalsCommand,  CustomFilters.sudo),
        "log":               (log,                  BotCommands.LogCommand,          CustomFilters.sudo),
        "restart_bot":       (restart_bot,          BotCommands.RestartCommand,      CustomFilters.sudo),

        # ── Core features ─────────────────────────────────────────────────
        "start":             (start,                BotCommands.StartCommand,        None),
        "leech":             (leech,                BotCommands.LeechCommand,        CustomFilters.authorized),
        "clone_node":        (clone_node,           BotCommands.CloneCommand,        CustomFilters.authorized),
        "cancel_all_buttons":(cancel_all_buttons,   BotCommands.CancelAllCommand,    CustomFilters.authorized),
        "select":            (select,               BotCommands.SelectCommand,       CustomFilters.authorized),
        "remove_from_queue": (remove_from_queue,    BotCommands.ForceStartCommand,   CustomFilters.authorized),
        "torrent_search":    (torrent_search,       BotCommands.SearchCommand,       CustomFilters.authorized),
        "get_rss_menu":      (get_rss_menu,         BotCommands.RssCommand,          CustomFilters.authorized),
        "task_status":       (task_status,          BotCommands.StatusCommand,       CustomFilters.authorized),
        "bot_help":          (bot_help,             BotCommands.HelpCommand,         CustomFilters.authorized),
        "bot_stats":         (bot_stats,            BotCommands.StatsCommand,        CustomFilters.authorized),
        "ping":              (ping,                 BotCommands.PingCommand,         CustomFilters.authorized),
        "send_user_settings":(send_user_settings,   BotCommands.UserSetCommand,      CustomFilters.authorized),
        "get_users_settings":(get_users_settings,   BotCommands.UsersCommand,        CustomFilters.sudo),
        "mediainfo":         (mediainfo,            BotCommands.MediaInfoCommand,    CustomFilters.authorized),
        "speedtest":         (speedtest,            BotCommands.SpeedTest,           CustomFilters.authorized),
        "spectrum_handler":  (spectrum_handler,     BotCommands.SoxCommand,          CustomFilters.authorized),

        # ── Media Tools ───────────────────────────────────────────────────
    }

    for handler_func, command_name, custom_filter in command_filters.values():
        if custom_filter:
            f = command(command_name, case_sensitive=True) & custom_filter
        else:
            f = command(command_name, case_sensitive=True)
        TgClient.bot.add_handler(MessageHandler(handler_func, filters=f))

    regex_filters = {
        "^botset":    edit_bot_settings,
        "^canall":    cancel_all_update,
        "^stopm":     cancel_multi,
        "^sel":       confirm_selection,
        "^rss":       rss_listener,
        "^torser":    torrent_search_update,
        "^userset":   edit_user_settings,
        "^help":      arg_usage,
        "^status":    status_pages,
        "^botrestart":confirm_restart,
        "^aeon":      aeon_callback,
        # Note: media_tool callbacks self-register via client_compat
    }

    for rf, handler_func in regex_filters.items():
        TgClient.bot.add_handler(CallbackQueryHandler(handler_func, filters=regex(rf)))

    TgClient.bot.add_handler(
        EditedMessageHandler(
            run_shell,
            filters=command(BotCommands.ShellCommand, case_sensitive=True) & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            cancel,
            filters=regex(r"^/stop(_\w+)?(?!all)") & CustomFilters.authorized,
        )
    )
