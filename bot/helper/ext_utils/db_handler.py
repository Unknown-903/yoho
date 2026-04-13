from importlib import import_module

from aiofiles import open as aiopen
from aiofiles.os import path as aiopath
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError
from pymongo.server_api import ServerApi

from bot import LOGGER, qbit_options, rss_dict, user_data
from bot.core.config_manager import Config
from bot.core.telegram_manager import TgClient


class DbManager:
    def __init__(self):
        self._return = True
        self._conn = None
        self.db = None

    async def connect(self):
        try:
            if self._conn is not None:
                self._conn.close()

            self._conn = AsyncIOMotorClient(
                Config.DATABASE_URL,
                server_api=ServerApi("1"),
                connectTimeoutMS=60000,
                serverSelectionTimeoutMS=60000,
            )

            self.db = self._conn.luna
            self._return = False
            LOGGER.info("Successfully connected to the database.")

        except PyMongoError as e:
            LOGGER.error(f"Error in DB connection: {e}")
            self.db = None
            self._return = True
            self._conn = None

    async def disconnect(self):
        self._return = True
        if self._conn is not None:
            self._conn.close()
            LOGGER.info("Database connection closed.")
        self._conn = None

    # ✅ FIXED FUNCTIONS
    async def get_bot_settings(self):
        if self._return:
            return {}
        try:
            data = await self.db.settings.config.find_one({"_id": TgClient.ID})
            return data or {}
        except Exception as e:
            LOGGER.error(f"Error getting bot settings: {e}")
            return {}

    async def get_users_data(self):
        if self._return:
            return {}
        try:
            users = {}
            async for doc in self.db.users.find({}):
                users[doc["_id"]] = doc
            return users
        except Exception as e:
            LOGGER.error(f"Error getting users data: {e}")
            return {}

    async def update_config(self, dict_):
        if self._return:
            return
        await self.db.settings.config.update_one(
            {"_id": TgClient.ID},
            {"$set": dict_},
            upsert=True,
        )

    async def update_qbittorrent(self, key, value):
        if self._return:
            return
        await self.db.settings.qbittorrent.update_one(
            {"_id": TgClient.ID},
            {"$set": {key: value}},
            upsert=True,
        )

    async def save_qbit_settings(self):
        if self._return:
            return
        await self.db.settings.qbittorrent.update_one(
            {"_id": TgClient.ID},
            {"$set": qbit_options},
            upsert=True,
        )

    async def update_user_data(self, user_id):
        if self._return:
            return
        data = user_data.get(user_id, {}).copy()
        for key in ("THUMBNAIL", "RCLONE_CONFIG", "TOKEN_PICKLE", "TOKEN", "TIME"):
            data.pop(key, None)

        await self.db.users.update_one(
            {"_id": user_id},
            {"$set": data},
            upsert=True,
        )

    async def rss_update(self, user_id):
        if self._return:
            return
        await self.db.rss[TgClient.ID].replace_one(
            {"_id": user_id},
            rss_dict[user_id],
            upsert=True,
        )

    async def rss_delete(self, user_id):
        if self._return:
            return
        await self.db.rss[TgClient.ID].delete_one({"_id": user_id})


database = DbManager()
