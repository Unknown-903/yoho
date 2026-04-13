# ruff: noqa: RUF012
from bot.core.config_manager import Config

i = Config.CMD_SUFFIX


class BotCommands:
    StartCommand      = "start"
    LeechCommand      = [f"leech{i}", f"l{i}"]
    CloneCommand      = f"clone{i}"
    MediaInfoCommand  = f"mediainfo{i}"
    CancelAllCommand  = f"cancelall{i}"
    ForceStartCommand = [f"forcestart{i}", f"fs{i}"]
    SearchCommand     = f"search{i}"
    StatusCommand     = [f"status{i}", "statusall"]
    UsersCommand      = f"users{i}"
    AuthorizeCommand  = f"auth{i}"
    UnAuthorizeCommand= f"unauth{i}"
    AddSudoCommand    = f"addsudo{i}"
    RmSudoCommand     = f"rmsudo{i}"
    PingCommand       = f"ping{i}"
    RestartCommand    = [f"restart{i}", "restartall"]
    StatsCommand      = f"stats{i}"
    HelpCommand       = f"help{i}"
    LogCommand        = f"log{i}"
    ShellCommand      = f"shell{i}"
    AExecCommand      = f"aexec{i}"
    ExecCommand       = f"exec{i}"
    ClearLocalsCommand= f"clearlocals{i}"
    BotSetCommand     = f"botsettings{i}"
    UserSetCommand    = f"settings{i}"
    SpeedTest         = f"speedtest{i}"
    BroadcastCommand  = [f"broadcast{i}", "broadcastall"]
    SelectCommand     = f"sel{i}"
    RssCommand        = f"rss{i}"
    SoxCommand        = [f"spectrum{i}", f"sox{i}"]

    # Media Tools (from Multi-Task-bot)
    EncodeCommand     = f"encode{i}"
    CompressCommand   = f"compress{i}"
    MergeCommand      = f"merge{i}"
    RenameCommand     = f"rename{i}"
    UpscaleCommand    = f"upscale{i}"
    AutoRenameCommand = f"autorename{i}"
    ExtractCommand    = f"extract{i}"
    QueueCommand      = [f"queue{i}", f"q{i}"]
    CancelCommand     = f"cancel{i}"
