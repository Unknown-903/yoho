# ruff: noqa: E402
from uvloop import install
install()

import os
import subprocess
from asyncio import Lock, new_event_loop, set_event_loop
from datetime import datetime
from logging import (
    ERROR, INFO, WARNING,
    FileHandler, Formatter, LogRecord, StreamHandler, basicConfig, getLogger,
)
from time import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pytz import timezone

getLogger("requests").setLevel(WARNING)
getLogger("urllib3").setLevel(WARNING)
getLogger("pyrogram").setLevel(ERROR)
getLogger("httpx").setLevel(WARNING)
getLogger("pymongo").setLevel(WARNING)
getLogger("aiohttp").setLevel(WARNING)

bot_start_time = time()

bot_loop = new_event_loop()
set_event_loop(bot_loop)


class CustomFormatter(Formatter):
    def formatTime(self, record: LogRecord, datefmt: str | None) -> str:
        dt: datetime = datetime.fromtimestamp(record.created, tz=timezone("Asia/Kolkata"))
        return dt.strftime(datefmt)

    def format(self, record: LogRecord) -> str:
        return super().format(record).replace(record.levelname, record.levelname[:1])


formatter = CustomFormatter(
    "[%(asctime)s] %(levelname)s - %(message)s [%(module)s:%(lineno)d]",
    datefmt="%d-%b %I:%M:%S %p",
)

file_handler = FileHandler("log.txt")
file_handler.setFormatter(formatter)
stream_handler = StreamHandler()
stream_handler.setFormatter(formatter)
basicConfig(handlers=[file_handler, stream_handler], level=INFO)

LOGGER = getLogger(__name__)

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/app/downloads/")

intervals = {
    "status": {},
    "stopAll": False,
}

# Global state dicts
user_data       = {}
queued_dl       = {}
queued_up       = {}
status_dict     = {}
task_dict       = {}
rss_dict        = {}
auth_chats      = {}
sudo_users      = []
multi_tags      = set()
non_queued_dl   = set()
non_queued_up   = set()
excluded_extensions = []
included_extensions = []
shorteners_list = []
drives_names    = []
drives_ids      = []
index_urls      = []

# Locks
task_dict_lock      = Lock()
queue_dict_lock     = Lock()
cpu_eater_lock      = Lock()
same_directory_lock = Lock()

scheduler = AsyncIOScheduler(event_loop=bot_loop)
