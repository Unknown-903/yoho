"""
Merged Database Handler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Combines Aeon's bot-settings/RSS/user-data management
with Multi-Task-bot's media-settings (encode/compress/rename/etc.)
into a single MongoDB-backed class.

Exports:
  database      → Used by Aeon modules (startup, task_listener, etc.)
  codeflixbots  → Used by media_tools plugins (same object, alias)
"""

import datetime
import logging
from importlib import import_module

from aiofiles import open as aiopen
from aiofiles.os import path as aiopath
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError
from pymongo.server_api import ServerApi

from bot import LOGGER, rss_dict, user_data

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cfg():
    from bot.core.config_manager import Config
    return Config


def _tg():
    from bot.core.telegram_manager import TgClient
    return TgClient


# ─────────────────────────────────────────────────────────────────────────────
# DbManager — unified class
# ─────────────────────────────────────────────────────────────────────────────

class DbManager:
    """
    Unified MongoDB manager for the merged bot.
    Handles both Aeon's infrastructure data and media-tool user settings.
    """

    def __init__(self):
        self._return = True
        self._conn   = None
        self.db      = None

    # ── Connection ────────────────────────────────────────────────────────────

    async def connect(self):
        cfg = _cfg()
        if not cfg.DATABASE_URL:
            LOGGER.warning("DATABASE_URL not set — DB features disabled.")
            return
        try:
            if self._conn is not None:
                await self._conn.close()
            self._conn = AsyncMongoClient(
                cfg.DATABASE_URL,
                server_api=ServerApi("1"),
                connectTimeoutMS=60000,
                serverSelectionTimeoutMS=60000,
            )
            self.db      = self._conn.merged_bot
            self._return = False
            LOGGER.info("Connected to MongoDB.")
        except PyMongoError as e:
            LOGGER.error(f"DB connection error: {e}")
            self.db      = None
            self._return = True
            self._conn   = None

    async def disconnect(self):
        self._return = True
        if self._conn is not None:
            await self._conn.close()
            LOGGER.info("DB connection closed.")
        self._conn = None

    # ── Bot settings ─────────────────────────────────────────────────────────

    async def update_deploy_config(self):
        if self._return:
            return
        try:
            settings    = import_module("config")
            config_file = {
                k: v.strip() if isinstance(v, str) else v
                for k, v in vars(settings).items()
                if not k.startswith("__")
            }
            await self.db.settings.deployConfig.replace_one(
                {"_id": _tg().ID}, config_file, upsert=True
            )
        except Exception as e:
            LOGGER.error(f"update_deploy_config: {e}")

    async def update_config(self, dict_):
        if self._return:
            return
        await self.db.settings.config.update_one(
            {"_id": _tg().ID}, {"$set": dict_}, upsert=True
        )

    async def get_bot_settings(self) -> dict:
        if self._return:
            return {}
        doc = await self.db.settings.config.find_one({"_id": _tg().ID})
        return doc or {}

    async def save_bot_settings(self, config_dict: dict):
        if self._return:
            return
        await self.db.settings.config.replace_one(
            {"_id": _tg().ID}, config_dict, upsert=True
        )

    async def update_private_file(self, path: str):
        if self._return:
            return
        db_path = path.replace(".", "__")
        if await aiopath.exists(path):
            async with aiopen(path, "rb+") as pf:
                pf_bin = await pf.read()
            await self.db.settings.files.update_one(
                {"_id": _tg().ID}, {"$set": {db_path: pf_bin}}, upsert=True
            )
            if path == "config.py":
                await self.update_deploy_config()
        else:
            await self.db.settings.files.update_one(
                {"_id": _tg().ID}, {"$unset": {db_path: ""}}, upsert=True
            )

    # ── Auth / sudo ───────────────────────────────────────────────────────────

    async def get_auth_chats(self) -> dict:
        if self._return:
            return {}
        doc = await self.db.settings.auth.find_one({"_id": _tg().ID})
        return doc.get("chats", {}) if doc else {}

    async def get_sudo_users(self) -> list:
        if self._return:
            return []
        doc = await self.db.settings.auth.find_one({"_id": _tg().ID})
        return doc.get("sudo", []) if doc else []

    async def save_auth_chats(self, chats: dict):
        if self._return:
            return
        await self.db.settings.auth.update_one(
            {"_id": _tg().ID}, {"$set": {"chats": chats}}, upsert=True
        )

    async def save_sudo_users(self, sudo: list):
        if self._return:
            return
        await self.db.settings.auth.update_one(
            {"_id": _tg().ID}, {"$set": {"sudo": sudo}}, upsert=True
        )

    # ── User data (Aeon style) ────────────────────────────────────────────────

    async def get_users_data(self) -> dict:
        if self._return:
            return {}
        result = {}
        async for doc in self.db.users.find({}):
            uid = doc.pop("_id")
            result[uid] = doc
        return result

    async def update_user_data(self, user_id: int):
        if self._return:
            return
        data = user_data.get(user_id, {}).copy()
        for key in ("THUMBNAIL", "TOKEN", "TIME"):
            data.pop(key, None)
        await self.db.users.update_one(
            {"_id": user_id}, {"$set": data}, upsert=True
        )

    async def update_user_doc(self, user_id: int, key: str, path: str = ""):
        if self._return:
            return
        if path:
            async with aiopen(path, "rb+") as doc:
                doc_bin = await doc.read()
            await self.db.users.update_one(
                {"_id": user_id}, {"$set": {key: doc_bin}}, upsert=True
            )
        else:
            await self.db.users.update_one(
                {"_id": user_id}, {"$unset": {key: ""}}, upsert=True
            )

    # ── RSS ───────────────────────────────────────────────────────────────────

    async def get_rss_data(self) -> dict:
        if self._return:
            return {}
        result = {}
        async for doc in self.db.rss.find({}):
            uid = doc.pop("_id")
            result[uid] = doc
        return result

    async def rss_update(self, user_id: int):
        if self._return:
            return
        await self.db.rss.replace_one(
            {"_id": user_id}, rss_dict[user_id], upsert=True
        )

    async def rss_update_all(self):
        if self._return:
            return
        for uid in list(rss_dict.keys()):
            await self.db.rss.replace_one(
                {"_id": uid}, rss_dict[uid], upsert=True
            )

    async def rss_delete(self, user_id: int):
        if self._return:
            return
        await self.db.rss.delete_one({"_id": user_id})

    # ── Incomplete tasks ──────────────────────────────────────────────────────

    async def add_incomplete_task(self, cid: int, link: str, tag: str):
        if self._return:
            return
        await self.db.tasks.insert_one({"_id": link, "cid": cid, "tag": tag})

    async def rm_complete_task(self, link: str):
        if self._return:
            return
        await self.db.tasks.delete_one({"_id": link})

    async def get_incomplete_tasks(self) -> dict:
        result = {}
        if self._return:
            return result
        async for row in self.db.tasks.find({}):
            cid = row["cid"]
            tag = row["tag"]
            lid = row["_id"]
            result.setdefault(cid, {}).setdefault(tag, []).append(lid)
        await self.db.tasks.drop()
        return result

    # ── Access tokens (Aeon premium) ──────────────────────────────────────────

    async def update_user_tdata(self, user_id: int, token: str, time_val):
        if self._return:
            return
        await self.db.access_token.update_one(
            {"_id": user_id},
            {"$set": {"TOKEN": token, "TIME": time_val}},
            upsert=True,
        )

    async def get_token_expiry(self, user_id: int):
        if self._return:
            return None
        doc = await self.db.access_token.find_one({"_id": user_id})
        return doc.get("TIME") if doc else None

    async def get_user_token(self, user_id: int):
        if self._return:
            return None
        doc = await self.db.access_token.find_one({"_id": user_id})
        return doc.get("TOKEN") if doc else None

    async def delete_user_token(self, user_id: int):
        if self._return:
            return
        await self.db.access_token.delete_one({"_id": user_id})

    async def delete_all_access_tokens(self):
        if self._return:
            return
        await self.db.access_token.delete_many({})

    # ── PM users ──────────────────────────────────────────────────────────────

    async def get_pm_uids(self):
        if self._return:
            return None
        return [doc["_id"] async for doc in self.db.pm_users.find({})]

    async def update_pm_users(self, user_id: int):
        if self._return:
            return
        if not await self.db.pm_users.find_one({"_id": user_id}):
            await self.db.pm_users.insert_one({"_id": user_id})

    async def rm_pm_user(self, user_id: int):
        if self._return:
            return
        await self.db.pm_users.delete_one({"_id": user_id})

    async def trunc_table(self, name: str):
        if self._return:
            return
        await self.db[name].drop()

    # ─────────────────────────────────────────────────────────────────────────
    # Media-tool user settings (from Multi-Task-bot)
    # All stored under db.media_users collection per user_id
    # ─────────────────────────────────────────────────────────────────────────

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _new_user(self, user_id: int) -> dict:
        today = datetime.date.today().isoformat()
        return {
            "_id": int(user_id),
            "join_date": today,
            "file_id": None,
            "caption": None,
            "metadata": True,
            "metadata_code": "",
            "format_template": None,
            "media_type": "document",
            "caption_style": "regular",
            "video_extension": "mkv",
            "title": "", "author": "", "artist": "", "album": "",
            "genre": "", "publisher": "", "encoded_by": "", "comment": "",
            "channel": "", "license": "", "copyright": "", "description": "",
            "audio": ["Japanese Audio|jpn","English Audio|eng","Hindi Audio|hin"],
            "subtitle": ["English Subtitles|eng"],
            "video": "",
            "rename_count": 0,
            "username": None,
            "task_counts": {},
        }

    async def _set(self, user_id: int, key: str, value):
        if self._return:
            return
        await self.db.media_users.update_one(
            {"_id": int(user_id)},
            {"$set": {key: value}},
            upsert=True,
        )

    async def _get(self, user_id: int, key: str, default=None):
        if self._return:
            return default
        doc = await self.db.media_users.find_one(
            {"_id": int(user_id)}, {key: 1}
        )
        if doc and key in doc:
            return doc[key]
        return default

    # ── User lifecycle ────────────────────────────────────────────────────────

    async def ensure_user(self, user_id: int, username=None):
        if self._return:
            return
        uid = int(user_id)
        if not await self.db.media_users.find_one({"_id": uid}):
            new = self._new_user(uid)
            if username:
                new["username"] = username
            await self.db.media_users.insert_one(new)
        elif username:
            await self._set(uid, "username", username)

    async def is_user_exist(self, user_id: int) -> bool:
        if self._return:
            return False
        return bool(await self.db.media_users.find_one({"_id": int(user_id)}))

    async def total_users_count(self) -> int:
        if self._return:
            return 0
        return await self.db.media_users.count_documents({})

    async def get_all_users(self):
        if self._return:
            return []
        return [doc async for doc in self.db.media_users.find({})]

    async def delete_user(self, user_id: int):
        if self._return:
            return
        await self.db.media_users.delete_one({"_id": int(user_id)})

    # ── Thumbnail & caption ───────────────────────────────────────────────────

    async def set_thumbnail(self, user_id, file_id):
        await self._set(user_id, "file_id", file_id)

    async def get_thumbnail(self, user_id):
        return await self._get(user_id, "file_id")

    async def set_caption(self, user_id, caption):
        await self._set(user_id, "caption", caption)

    async def get_caption(self, user_id):
        return await self._get(user_id, "caption")

    async def set_caption_format(self, user_id, fmt):
        await self._set(user_id, "caption_format", fmt)

    async def get_caption_format(self, user_id):
        return await self._get(user_id, "caption_format", "default")

    async def set_format_template(self, user_id, template):
        await self._set(user_id, "format_template", template)

    async def get_format_template(self, user_id):
        return await self._get(user_id, "format_template")

    async def get_rename_format(self, user_id):
        return await self._get(user_id, "rename_format", "")

    async def set_rename_format(self, user_id, template):
        await self._set(user_id, "rename_format", template)

    async def set_media_preference(self, user_id, media_type):
        await self._set(user_id, "media_type", media_type)

    async def get_media_preference(self, user_id):
        return await self._get(user_id, "media_type", "document")

    async def set_caption_style(self, user_id, style):
        await self._set(user_id, "caption_style", style)

    async def get_caption_style(self, user_id):
        return await self._get(user_id, "caption_style", "regular")

    async def set_video_extension(self, user_id, extension):
        await self._set(user_id, "video_extension", extension)

    async def get_video_extension(self, user_id):
        return await self._get(user_id, "video_extension", "mkv")

    # ── Metadata ──────────────────────────────────────────────────────────────

    async def set_metadata(self, user_id, metadata):
        await self._set(user_id, "metadata", metadata)

    async def get_metadata(self, user_id):
        return await self._get(user_id, "metadata", True)

    async def get_metadata_code(self, user_id):
        return await self._get(user_id, "metadata_code", "")

    async def set_metadata_code(self, user_id, code):
        await self._set(user_id, "metadata_code", code)

    # Metadata tag setters/getters (title/author/artist/album/genre/publisher/etc.)
    _META_TAGS = [
        "title","author","artist","album","genre","publisher",
        "encoded_by","comment","channel","license","copyright","description",
    ]

    def __getattr__(self, name: str):
        """Auto-generate get_X / set_X for metadata tags."""
        for tag in self._META_TAGS:
            if name == f"get_{tag}":
                async def _getter(uid, _t=tag):
                    return await self._get(uid, _t, "")
                return _getter
            if name == f"set_{tag}":
                async def _setter(uid, val, _t=tag):
                    await self._set(uid, _t, val)
                return _setter
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    # ── Watermark ─────────────────────────────────────────────────────────────

    async def set_watermark_text(self, user_id, text):
        await self._set(user_id, "watermark_text", text)

    async def get_watermark_text(self, user_id):
        return await self._get(user_id, "watermark_text", "")

    async def set_watermark_image(self, user_id, file_id):
        await self._set(user_id, "watermark_image", file_id)

    async def get_watermark_image(self, user_id):
        return await self._get(user_id, "watermark_image", "")

    async def set_watermark_position(self, user_id, position):
        await self._set(user_id, "watermark_position", position)

    async def get_watermark_position(self, user_id):
        return await self._get(user_id, "watermark_position", "top_right")

    async def set_watermark_size(self, user_id, size):
        await self._set(user_id, "watermark_size", size)

    async def get_watermark_size(self, user_id):
        return await self._get(user_id, "watermark_size", "medium")

    async def set_watermark_opacity(self, user_id, opacity):
        await self._set(user_id, "watermark_opacity", opacity)

    async def get_watermark_opacity(self, user_id):
        return await self._get(user_id, "watermark_opacity", 0.7)

    async def set_watermark_mode(self, user_id, mode):
        await self._set(user_id, "watermark_mode", mode)

    async def get_watermark_mode(self, user_id):
        return await self._get(user_id, "watermark_mode", "text")

    async def get_watermark_color(self, user_id):
        return await self._get(user_id, "watermark_color", "white")

    async def set_watermark_color(self, user_id, color):
        await self._set(user_id, "watermark_color", color)

    async def get_watermark_style(self, user_id):
        return await self._get(user_id, "watermark_style", "shadow")

    async def set_watermark_style(self, user_id, style):
        await self._set(user_id, "watermark_style", style)

    async def get_watermark_apply(self, user_id):
        return await self._get(user_id, "watermark_apply", "ask")

    async def set_watermark_apply(self, user_id, mode):
        await self._set(user_id, "watermark_apply", mode)

    async def get_wm_process(self, user_id, process):
        toggles = await self._get(user_id, "wm_per_process", {})
        return (toggles or {}).get(process, "ask")

    async def set_wm_process(self, user_id, process, state):
        toggles = await self._get(user_id, "wm_per_process", {}) or {}
        toggles[process] = state
        await self._set(user_id, "wm_per_process", toggles)

    async def get_all_wm_processes(self, user_id):
        toggles = await self._get(user_id, "wm_per_process", {}) or {}
        defaults = {
            "encode":"ask","compress":"ask","merge":"ask",
            "rename":"ask","autorename":"off","upscale":"ask"
        }
        return {**defaults, **toggles}

    # ── Audio / Subtitle / Video tags ─────────────────────────────────────────

    async def get_audio(self, user_id):
        return await self._get(user_id, "audio", [])

    async def set_audio(self, user_id, audio):
        await self._set(user_id, "audio", audio)

    async def get_subtitle(self, user_id):
        return await self._get(user_id, "subtitle", [])

    async def set_subtitle(self, user_id, subtitle):
        await self._set(user_id, "subtitle", subtitle)

    async def get_video_tag(self, user_id):
        return await self._get(user_id, "video", "")

    async def set_video_tag(self, user_id, video):
        await self._set(user_id, "video", video)

    # ── Subtitle mode ─────────────────────────────────────────────────────────

    async def get_subtitle_mode(self, user_id):
        return await self._get(user_id, "subtitle_mode", "none")

    async def set_subtitle_mode(self, user_id, mode):
        await self._set(user_id, "subtitle_mode", mode)

    # ── Encode settings ───────────────────────────────────────────────────────

    async def get_encode_codec(self, user_id):
        return await self._get(user_id, "encode_codec", "ask")

    async def set_encode_codec(self, user_id, codec):
        await self._set(user_id, "encode_codec", codec)

    async def get_encode_resolution(self, user_id):
        return await self._get(user_id, "encode_resolution", "ask")

    async def set_encode_resolution(self, user_id, res):
        await self._set(user_id, "encode_resolution", res)

    async def get_encode_preset(self, user_id):
        return await self._get(user_id, "encode_preset", "ask")

    async def set_encode_preset(self, user_id, preset):
        await self._set(user_id, "encode_preset", preset)

    async def get_encode_crf(self, user_id):
        return await self._get(user_id, "encode_crf", None)

    async def set_encode_crf(self, user_id, crf):
        await self._set(user_id, "encode_crf", crf)

    async def get_encode_10bit(self, user_id):
        return await self._get(user_id, "encode_10bit", False)

    async def set_encode_10bit(self, user_id, enabled):
        await self._set(user_id, "encode_10bit", enabled)

    async def get_encode_audio_codec(self, user_id):
        return await self._get(user_id, "encode_audio_codec", "ask")

    async def set_encode_audio_codec(self, user_id, codec):
        await self._set(user_id, "encode_audio_codec", codec)

    async def get_encode_audio_bitrate(self, user_id):
        return await self._get(user_id, "encode_audio_bitrate", "128k")

    async def set_encode_audio_bitrate(self, user_id, bitrate):
        await self._set(user_id, "encode_audio_bitrate", bitrate)

    async def get_encode_audio_channels(self, user_id):
        return await self._get(user_id, "encode_audio_channels", "original")

    async def set_encode_audio_channels(self, user_id, channels):
        await self._set(user_id, "encode_audio_channels", channels)

    async def get_encode_audio_samplerate(self, user_id):
        return await self._get(user_id, "encode_audio_samplerate", "original")

    async def set_encode_audio_samplerate(self, user_id, rate):
        await self._set(user_id, "encode_audio_samplerate", rate)

    async def get_encode_compress(self, user_id):
        return await self._get(user_id, "encode_compress", "ask")

    async def set_encode_compress(self, user_id, level):
        await self._set(user_id, "encode_compress", level)

    # ── AF (Audio Filter) per-process ─────────────────────────────────────────

    async def get_af_process(self, user_id, process):
        toggles = await self._get(user_id, "af_per_process", {}) or {}
        return toggles.get(process, "ask")

    async def set_af_process(self, user_id, process, state):
        toggles = await self._get(user_id, "af_per_process", {}) or {}
        toggles[process] = state
        await self._set(user_id, "af_per_process", toggles)

    async def get_all_af_processes(self, user_id):
        toggles = await self._get(user_id, "af_per_process", {}) or {}
        defaults = {
            "encode":"ask","compress":"ask","merge":"off",
            "rename":"off","autorename":"off"
        }
        return {**defaults, **toggles}

    # ── Replacor ──────────────────────────────────────────────────────────────

    async def get_replacor_strings(self, user_id):
        return await self._get(user_id, "replacor_strings", [])

    async def set_replacor_strings(self, user_id, strings):
        await self._set(user_id, "replacor_strings", strings)

    async def add_replacor_string(self, user_id, string):
        strings = await self.get_replacor_strings(user_id)
        s = string.strip()
        if s.lower() not in [x.lower() for x in strings]:
            strings.append(s)
            await self.set_replacor_strings(user_id, strings)
        return strings

    async def remove_replacor_string(self, user_id, string):
        strings = await self.get_replacor_strings(user_id)
        strings = [x for x in strings if x.lower() != string.lower()]
        await self.set_replacor_strings(user_id, strings)
        return strings

    async def get_replacor_final(self, user_id):
        return await self._get(user_id, "replacor_final", "")

    async def set_replacor_final(self, user_id, final):
        await self._set(user_id, "replacor_final", final)

    async def get_replacor_enabled(self, user_id):
        return await self._get(user_id, "replacor_enabled", False)

    async def set_replacor_enabled(self, user_id, enabled):
        await self._set(user_id, "replacor_enabled", enabled)

    # ── Task counts / leaderboard ─────────────────────────────────────────────

    async def increment_task_count(self, user_id, task_type: str):
        if self._return:
            return
        await self.db.media_users.update_one(
            {"_id": int(user_id)},
            {"$inc": {f"task_counts.{task_type}": 1}},
            upsert=True,
        )

    async def get_task_counts(self, user_id) -> dict:
        return await self._get(user_id, "task_counts", {})

    async def increment_rename_count(self, user_id):
        if self._return:
            return
        await self.db.media_users.update_one(
            {"_id": int(user_id)}, {"$inc": {"rename_count": 1}}, upsert=True
        )

    async def get_rename_count(self, user_id) -> int:
        return await self._get(user_id, "rename_count", 0)

    async def get_leaderboard(self, limit=10, task_type=None):
        if self._return:
            return []
        pipeline = [{"$project": {"task_counts": 1}}]
        if task_type:
            pipeline.append({
                "$addFields": {"total": f"$task_counts.{task_type}"}
            })
        else:
            pipeline.append({
                "$addFields": {
                    "total": {"$sum": {"$objectToArray": "$task_counts"}}
                }
            })
        pipeline += [
            {"$sort": {"total": -1}},
            {"$limit": limit},
        ]
        return [doc async for doc in self.db.media_users.aggregate(pipeline)]

    async def get_user_rank(self, user_id) -> int:
        if self._return:
            return 0
        counts = await self.get_task_counts(user_id)
        total  = sum(counts.values())
        rank   = await self.db.media_users.count_documents(
            {"task_counts_total": {"$gt": total}}
        )
        return rank + 1

    # ── FSUB / Premium ────────────────────────────────────────────────────────

    async def get_fsub_channels(self) -> list:
        if self._return:
            return []
        doc = await self.db.bot_config.find_one({"_id": "fsub"})
        return doc.get("channels", []) if doc else []

    async def set_fsub_channels(self, channels: list):
        if self._return:
            return
        await self.db.bot_config.update_one(
            {"_id": "fsub"}, {"$set": {"channels": channels}}, upsert=True
        )

    async def get_premium_users(self) -> list:
        if self._return:
            return []
        return [doc["_id"] async for doc in self.db.premium_users.find({})]

    async def add_premium_user(self, user_id: int):
        if self._return:
            return
        if not await self.db.premium_users.find_one({"_id": user_id}):
            await self.db.premium_users.insert_one({"_id": user_id})

    async def remove_premium_user(self, user_id: int):
        if self._return:
            return
        await self.db.premium_users.delete_one({"_id": user_id})

    async def is_premium_user(self, user_id: int) -> bool:
        if self._return:
            return False
        return bool(await self.db.premium_users.find_one({"_id": user_id}))



    async def update_aria2(self, key: str, value):
        """Stub: aria2 removed, kept for compatibility with bot_settings.py"""
        if self._return:
            return
        await self.db.settings.aria2c.update_one(
            {"_id": _tg().ID}, {"$set": {key: value}}, upsert=True
        )

    async def update_nzb_config(self):
        """Stub: NZB removed, kept for compatibility with bot_settings.py"""
        pass

    async def update_user_token(self, user_id: int, token: str):
        """Update access token for user (used by access_check.py)."""
        if self._return:
            return
        await self.db.access_token.update_one(
            {"_id": user_id},
            {"$set": {"TOKEN": token}},
            upsert=True,
        )

    async def get_user_rank(self, user_id: int) -> int:
        if self._return:
            return 0
        user_counts = await self.get_task_counts(user_id)
        user_total  = sum(user_counts.values())
        rank = 1
        async for doc in self.db.media_users.find({}, {"task_counts": 1}):
            counts = doc.get("task_counts", {})
            total  = sum(counts.values())
            if total > user_total:
                rank += 1
        return rank

# ─────────────────────────────────────────────────────────────────────────────
# Singleton exports
# ─────────────────────────────────────────────────────────────────────────────

database     = DbManager()   # Used by Aeon modules
codeflixbots = database      # Used by media_tools plugins (same object)
