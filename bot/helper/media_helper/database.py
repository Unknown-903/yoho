import datetime
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from bot.core.config_manager import Config
from .utils import send_log

logger = logging.getLogger(__name__)


class Database:

    def __init__(self, uri: str, db_name: str):

        self._client = AsyncIOMotorClient(uri)
        self.db = self._client[db_name]
        self.col = self.db.user

        # ===== COLLECTIONS =====
        self.fsub_channels = self.db.fsub_channels
        self.premium_users = self.db.premium_users
        self.sudo_users_col = self.db.sudo_users
        self.auth_chats_col = self.db.auth_chats
        self.verification_data = self.db.verification
        self.encode_settings = self.db.encode_settings

        logger.info(f"MongoDB Connected → {db_name}")


# ================= USER TEMPLATE =================

    def new_user(self, user_id: int):

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

            "title": "",
            "author": "",
            "artist": "",
            "album": "",
            "genre": "",
            "publisher": "",
            "encoded_by": "",
            "comment": "",
            "channel": "",
            "license": "",
            "copyright": "",
            "description": "",

            "audio": [
                "Japanese Audio|jpn",
                "English Audio|eng",
                "Hindi Audio|hin",
                "Tamil Audio|tam",
                "Telugu Audio|tel"
            ],

            "subtitle": [
                "English Subtitles|eng"
            ],

            "video": "",

            # ===== TASK TRACKING =====
            "rename_count": 0,
            "username": None,
            "task_counts": {},

            # ===== WATERMARK =====
            "watermark_text": None,
            "watermark_image_id": None,
            "watermark_position": "top_right",   # top_left, top_right, bottom_left, bottom_right, center
            "watermark_size": "medium",           # small, medium, large
            "watermark_opacity": 0.7,
            "watermark_mode": "text",             # text, image, both
            "watermark_color": "white",           # white, yellow, red, cyan, lime, gold, hotpink, custom hex
            "watermark_style": "shadow",          # shadow, outline, glow, neon, clean, bold

            # ===== SUBTITLE MODE =====
            "subtitle_mode": "copy",              # copy, hardsub, none

            # ===== CAPTION FORMATTING =====
            "caption_format": "custom",           # custom, as_original

            # ===== REPLACOR =====
            "replacor_strings": [],              # list of strings to replace (case-insensitive)
            "replacor_final": "",                # replacement string
            "replacor_enabled": False,           # on/off

            # ===== WATERMARK APPLY MODE =====
            "watermark_apply": "ask",            # on, off, ask (per process)

            # ===== PER-PROCESS WATERMARK TOGGLE =====
            "wm_per_process": {
                "encode": "ask", "compress": "ask", "merge": "ask",
                "rename": "ask", "autorename": "off", "upscale": "ask",
            },

            # ===== PER-PROCESS AUDIO REORDER (AF) TOGGLE =====
            "af_per_process": {
                "encode": "off", "compress": "off", "merge": "off",        # set "off", "ask" or "on"
                "rename": "off", "autorename": "off",
            },

            "ban_status": {
                "is_banned": False,
                "ban_duration": 0,
                "banned_on": datetime.date.max.isoformat(),
                "ban_reason": ""
            }
        }


# ================= USER MANAGEMENT =================

    async def add_user(self, bot, message):
        user = message.from_user
        if not await self.is_user_exist(user.id):
            try:
                new = self.new_user(user.id)
                new["username"] = user.username.lower() if user.username else None
                await self.col.insert_one(new)
                await send_log(bot, user)
                logger.info(f"New user added → {user.id}")
            except Exception as e:
                logger.error(e)
        else:
            if user.username:
                await self.col.update_one(
                    {"_id": int(user.id)},
                    {"$set": {"username": user.username.lower()}}
                )

    async def ensure_user(self, user_id, username=None):
        """Ensure user exists in DB. Create minimal record if not."""
        if not await self.is_user_exist(user_id):
            new = self.new_user(user_id)
            if username:
                new["username"] = username.lower()
            try:
                await self.col.insert_one(new)
                logger.info(f"Auto-created user → {user_id}")
            except Exception:
                pass

    async def is_user_exist(self, user_id: int):
        user = await self.col.find_one({"_id": int(user_id)})
        return bool(user)

    async def total_users_count(self):
        return await self.col.count_documents({})

    async def get_all_users(self):
        return self.col.find({})

    async def delete_user(self, user_id: int):
        await self.col.delete_many({"_id": int(user_id)})


# ================= GENERIC SET / GET =================

    async def _set(self, user_id, key, value):
        await self.col.update_one(
            {"_id": int(user_id)},
            {"$set": {key: value}},
            upsert=True
        )

    async def _get(self, user_id, key, default=None):
        user = await self.col.find_one({"_id": int(user_id)})
        if user:
            return user.get(key, default)
        return default


# ================= THUMBNAIL =================
    async def set_thumbnail(self, user_id, file_id):
        await self._set(user_id, "file_id", file_id)

    async def get_thumbnail(self, user_id):
        return await self._get(user_id, "file_id")

# ================= CAPTION =================
    async def set_caption(self, user_id, caption):
        await self._set(user_id, "caption", caption)

    async def get_caption(self, user_id):
        return await self._get(user_id, "caption")

# ================= CAPTION FORMAT =================
    async def set_caption_format(self, user_id, fmt):
        """fmt: 'custom' or 'as_original'"""
        await self._set(user_id, "caption_format", fmt)

    async def get_caption_format(self, user_id):
        return await self._get(user_id, "caption_format", "custom")

# ================= RENAME TEMPLATE =================
    async def set_format_template(self, user_id, template):
        await self._set(user_id, "format_template", template)

    async def get_format_template(self, user_id):
        return await self._get(user_id, "format_template")

    async def get_rename_format(self, user_id):
        return await self.get_format_template(user_id)

    async def set_rename_format(self, user_id, template):
        await self.set_format_template(user_id, template)

# ================= MEDIA TYPE =================
    async def set_media_preference(self, user_id, media_type):
        await self._set(user_id, "media_type", media_type)

    async def get_media_preference(self, user_id):
        return await self._get(user_id, "media_type", "document")

# ================= CAPTION STYLE =================
    async def set_caption_style(self, user_id, style):
        await self._set(user_id, "caption_style", style)

    async def get_caption_style(self, user_id):
        return await self._get(user_id, "caption_style", "regular")

# ================= VIDEO EXTENSION =================
    async def set_video_extension(self, user_id, extension):
        await self._set(user_id, "video_extension", extension)

    async def get_video_extension(self, user_id):
        return await self._get(user_id, "video_extension", "mkv")

# ================= METADATA =================
    async def set_metadata(self, user_id, metadata):
        await self._set(user_id, "metadata", metadata)

    async def get_metadata(self, user_id):
        return await self._get(user_id, "metadata", True)

# ================= TITLE / AUTHOR / ARTIST =================
    async def get_title(self, user_id):
        return await self._get(user_id, "title", "")

    async def set_title(self, user_id, title):
        await self._set(user_id, "title", title)

    async def get_author(self, user_id):
        return await self._get(user_id, "author", "")

    async def set_author(self, user_id, author):
        await self._set(user_id, "author", author)

    async def get_artist(self, user_id):
        return await self._get(user_id, "artist", "")

    async def set_artist(self, user_id, artist):
        await self._set(user_id, "artist", artist)

    async def get_album(self, user_id):
        return await self._get(user_id, "album", "")

    async def set_album(self, user_id, album):
        await self._set(user_id, "album", album)

    async def get_genre(self, user_id):
        return await self._get(user_id, "genre", "")

    async def set_genre(self, user_id, genre):
        await self._set(user_id, "genre", genre)

    async def get_publisher(self, user_id):
        return await self._get(user_id, "publisher", "")

    async def set_publisher(self, user_id, publisher):
        await self._set(user_id, "publisher", publisher)

    async def get_encoded_by(self, user_id):
        return await self._get(user_id, "encoded_by", "")

    async def set_encoded_by(self, user_id, encoded_by):
        await self._set(user_id, "encoded_by", encoded_by)

    async def get_comment(self, user_id):
        return await self._get(user_id, "comment", "")

    async def set_comment(self, user_id, comment):
        await self._set(user_id, "comment", comment)

    async def get_channel(self, user_id):
        return await self._get(user_id, "channel", "")

    async def set_channel(self, user_id, channel):
        await self._set(user_id, "channel", channel)

    async def get_license(self, user_id):
        return await self._get(user_id, "license", "")

    async def set_license(self, user_id, license):
        await self._set(user_id, "license", license)

    async def get_copyright(self, user_id):
        return await self._get(user_id, "copyright", "")

    async def set_copyright(self, user_id, copyright):
        await self._set(user_id, "copyright", copyright)

    async def get_description(self, user_id):
        return await self._get(user_id, "description", "")

    async def set_description(self, user_id, description):
        await self._set(user_id, "description", description)

# ================= AUDIO / SUBTITLE / VIDEO =================
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

    async def get_metadata_code(self, user_id):
        return await self._get(user_id, "metadata_code", "")

    async def set_metadata_code(self, user_id, code):
        await self._set(user_id, "metadata_code", code)


# ================= WATERMARK =================
    async def set_watermark_text(self, user_id, text):
        await self._set(user_id, "watermark_text", text)

    async def get_watermark_text(self, user_id):
        return await self._get(user_id, "watermark_text")

    async def set_watermark_image(self, user_id, file_id):
        await self._set(user_id, "watermark_image_id", file_id)

    async def get_watermark_image(self, user_id):
        return await self._get(user_id, "watermark_image_id")

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
        """mode: 'text', 'image', 'both'"""
        await self._set(user_id, "watermark_mode", mode)

    async def get_watermark_mode(self, user_id):
        return await self._get(user_id, "watermark_mode", "text")


# ================= SUBTITLE MODE =================
    async def set_subtitle_mode(self, user_id, mode):
        """mode: 'copy', 'hardsub', 'none'"""
        await self._set(user_id, "subtitle_mode", mode)

    async def get_subtitle_mode(self, user_id):
        return await self._get(user_id, "subtitle_mode", "copy")


# ================= TASK COUNTING (FIXED — upsert=True) =================

    async def increment_rename_count(self, user_id):
        """Increment rename count (backward compat)."""
        return await self.increment_task_count(user_id, "rename")

    async def get_rename_count(self, user_id):
        return await self._get(user_id, "rename_count", 0)

    async def increment_task_count(self, user_id, task_type):
        """Increment task count by type: rename, encode, compress, merge, upscale.
        FIXED: upsert=True so it works even if user doc was not created via /start."""
        field = f"task_counts.{task_type}"
        inc_fields = {field: 1}
        if task_type == "rename":
            inc_fields["rename_count"] = 1

        result = await self.col.find_one_and_update(
            {"_id": int(user_id)},
            {"$inc": inc_fields},
            return_document=True,
            upsert=True
        )
        if result:
            return result.get("task_counts", {}).get(task_type, 1)
        return 0

    async def get_task_counts(self, user_id):
        """Get all task counts for a user."""
        user = await self.col.find_one({"_id": int(user_id)})
        if user:
            return user.get("task_counts", {})
        return {}

    async def get_leaderboard(self, limit=10, task_type=None):
        """Top users. task_type=None means total across all types."""
        if task_type:
            field = f"task_counts.{task_type}"
            cursor = self.col.find(
                {field: {"$gt": 0}},
                {"_id": 1, "username": 1, "task_counts": 1, "rename_count": 1}
            ).sort(field, -1).limit(limit)
            return await cursor.to_list(length=limit)
        else:
            pipeline = [
                {"$addFields": {
                    "total_tasks": {
                        "$add": [
                            {"$ifNull": ["$task_counts.rename", 0]},
                            {"$ifNull": ["$task_counts.encode", 0]},
                            {"$ifNull": ["$task_counts.compress", 0]},
                            {"$ifNull": ["$task_counts.merge", 0]},
                            {"$ifNull": ["$task_counts.upscale", 0]},
                        ]
                    }
                }},
                {"$match": {"total_tasks": {"$gt": 0}}},
                {"$sort": {"total_tasks": -1}},
                {"$limit": limit},
                {"$project": {"_id": 1, "username": 1, "task_counts": 1, "total_tasks": 1}}
            ]
            cursor = self.col.aggregate(pipeline)
            return await cursor.to_list(length=limit)

    async def get_user_rank(self, user_id):
        """Get user's rank in overall leaderboard."""
        pipeline = [
            {"$addFields": {
                "total_tasks": {
                    "$add": [
                        {"$ifNull": ["$task_counts.rename", 0]},
                        {"$ifNull": ["$task_counts.encode", 0]},
                        {"$ifNull": ["$task_counts.compress", 0]},
                        {"$ifNull": ["$task_counts.merge", 0]},
                        {"$ifNull": ["$task_counts.upscale", 0]},
                    ]
                }
            }},
            {"$match": {"total_tasks": {"$gt": 0}}},
            {"$sort": {"total_tasks": -1}},
            {"$group": {"_id": None, "users": {"$push": "$_id"}}},
        ]
        result = await self.col.aggregate(pipeline).to_list(length=1)
        if result:
            users = result[0]["users"]
            if int(user_id) in users:
                return users.index(int(user_id)) + 1
        return None


# ================= BAN SYSTEM =================

    async def ban_user(self, user_id, reason="No reason", duration=0):
        await self.col.update_one(
            {"_id": int(user_id)},
            {"$set": {
                "ban_status.is_banned": True,
                "ban_status.ban_reason": reason,
                "ban_status.ban_duration": duration,
                "ban_status.banned_on": datetime.date.today().isoformat(),
            }}
        )

    async def unban_user(self, user_id):
        await self.col.update_one(
            {"_id": int(user_id)},
            {"$set": {
                "ban_status.is_banned": False,
                "ban_status.ban_reason": "",
                "ban_status.ban_duration": 0,
            }}
        )

    async def is_banned(self, user_id):
        user = await self.col.find_one({"_id": int(user_id)})
        if user:
            return user.get("ban_status", {}).get("is_banned", False)
        return False

    async def get_all_banned(self):
        cursor = self.col.find({"ban_status.is_banned": True}, {"_id": 1, "ban_status": 1})
        return await cursor.to_list(length=100)


# ================= PREMIUM SYSTEM =================

    async def add_premium(self, user_id, days=None):
        """Add premium access. days=None means unlimited."""
        data = {
            "user_id": int(user_id),
            "is_premium": True,
            "added_on": datetime.datetime.utcnow(),
        }
        if days and days > 0:
            data["expiry"] = datetime.datetime.utcnow() + datetime.timedelta(days=days)
        else:
            data["expiry"] = None

        await self.premium_users.update_one(
            {"user_id": int(user_id)},
            {"$set": data},
            upsert=True
        )

    async def remove_premium(self, user_id):
        await self.premium_users.delete_one({"user_id": int(user_id)})

    async def has_premium(self, user_id):
        doc = await self.premium_users.find_one({"user_id": int(user_id)})
        if not doc or not doc.get("is_premium"):
            return False
        expiry = doc.get("expiry")
        if expiry and datetime.datetime.utcnow() > expiry:
            await self.remove_premium(user_id)
            return False
        return True

    async def get_premium_remaining(self, user_id):
        doc = await self.premium_users.find_one({"user_id": int(user_id)})
        if not doc or not doc.get("is_premium"):
            return -1
        expiry = doc.get("expiry")
        if expiry is None:
            return None  # unlimited
        remaining = (expiry - datetime.datetime.utcnow()).total_seconds()
        if remaining <= 0:
            await self.remove_premium(user_id)
            return -1
        return remaining / 86400

    async def get_all_premium(self):
        cursor = self.premium_users.find({"is_premium": True})
        return await cursor.to_list(length=100)


# ================= FORCE SUBSCRIBE =================

    async def add_fsub_channel(self, channel_id, title="", invite_link="", username=""):
        await self.fsub_channels.update_one(
            {"channel_id": int(channel_id)},
            {"$set": {
                "channel_id": int(channel_id),
                "title": title,
                "invite_link": invite_link,
                "username": username,
            }},
            upsert=True
        )

    async def remove_fsub_channel(self, channel_id):
        await self.fsub_channels.delete_one({"channel_id": int(channel_id)})

    async def get_fsub_channels(self):
        cursor = self.fsub_channels.find({})
        return await cursor.to_list(length=50)


# ================= SUDO USERS =================

    async def add_sudo(self, user_id):
        await self.sudo_users_col.update_one(
            {"user_id": int(user_id)},
            {"$set": {"user_id": int(user_id)}},
            upsert=True
        )

    async def remove_sudo(self, user_id):
        await self.sudo_users_col.delete_one({"user_id": int(user_id)})

    async def get_all_sudo(self):
        cursor = self.sudo_users_col.find({})
        docs = await cursor.to_list(length=100)
        return [d["user_id"] for d in docs]

    async def is_sudo(self, user_id):
        doc = await self.sudo_users_col.find_one({"user_id": int(user_id)})
        return bool(doc)


# ================= AUTH CHATS =================

    async def add_auth_chat(self, chat_id):
        await self.auth_chats_col.update_one(
            {"chat_id": int(chat_id)},
            {"$set": {"chat_id": int(chat_id)}},
            upsert=True
        )

    async def remove_auth_chat(self, chat_id):
        await self.auth_chats_col.delete_one({"chat_id": int(chat_id)})

    async def get_all_auth_chats(self):
        cursor = self.auth_chats_col.find({})
        docs = await cursor.to_list(length=200)
        return [d["chat_id"] for d in docs]


# ================= ENCODE SETTINGS (per-user) =================

    async def set_encode_setting(self, user_id, key, value):
        await self.encode_settings.update_one(
            {"user_id": int(user_id)},
            {"$set": {key: value}},
            upsert=True
        )

    async def get_encode_setting(self, user_id, key, default=None):
        doc = await self.encode_settings.find_one({"user_id": int(user_id)})
        if doc:
            return doc.get(key, default)
        return default

    async def get_all_encode_settings(self, user_id):
        doc = await self.encode_settings.find_one({"user_id": int(user_id)})
        return doc or {}

    async def reset_encode_settings(self, user_id):
        await self.encode_settings.delete_one({"user_id": int(user_id)})

    # ===== Encode Setting Helpers =====
    # Each returns "ask" if user wants to be prompted, or the saved value

    async def get_encode_codec(self, user_id):
        return await self.get_encode_setting(user_id, "codec", "ask")

    async def set_encode_codec(self, user_id, codec):
        await self.set_encode_setting(user_id, "codec", codec)

    async def get_encode_resolution(self, user_id):
        return await self.get_encode_setting(user_id, "resolution", "ask")

    async def set_encode_resolution(self, user_id, res):
        await self.set_encode_setting(user_id, "resolution", res)

    async def get_encode_preset(self, user_id):
        return await self.get_encode_setting(user_id, "preset", "ask")

    async def set_encode_preset(self, user_id, preset):
        await self.set_encode_setting(user_id, "preset", preset)

    async def get_encode_crf(self, user_id):
        return await self.get_encode_setting(user_id, "crf", "ask")

    async def set_encode_crf(self, user_id, crf):
        await self.set_encode_setting(user_id, "crf", crf)

    async def get_encode_10bit(self, user_id):
        return await self.get_encode_setting(user_id, "ten_bit", False)

    async def set_encode_10bit(self, user_id, enabled):
        await self.set_encode_setting(user_id, "ten_bit", enabled)

    async def get_encode_audio_codec(self, user_id):
        return await self.get_encode_setting(user_id, "audio_codec", "ask")

    async def set_encode_audio_codec(self, user_id, codec):
        await self.set_encode_setting(user_id, "audio_codec", codec)

    async def get_encode_audio_bitrate(self, user_id):
        return await self.get_encode_setting(user_id, "audio_bitrate", "128k")

    async def set_encode_audio_bitrate(self, user_id, bitrate):
        await self.set_encode_setting(user_id, "audio_bitrate", bitrate)

    async def get_encode_audio_channels(self, user_id):
        return await self.get_encode_setting(user_id, "audio_channels", "ask")

    async def set_encode_audio_channels(self, user_id, channels):
        await self.set_encode_setting(user_id, "audio_channels", channels)

    async def get_encode_audio_samplerate(self, user_id):
        return await self.get_encode_setting(user_id, "audio_samplerate", "ask")

    async def set_encode_audio_samplerate(self, user_id, rate):
        await self.set_encode_setting(user_id, "audio_samplerate", rate)

    async def get_encode_compress(self, user_id):
        return await self.get_encode_setting(user_id, "compress_level", "ask")

    async def set_encode_compress(self, user_id, level):
        await self.set_encode_setting(user_id, "compress_level", level)


# ================= REPLACOR =================
    async def get_replacor_strings(self, user_id):
        return await self._get(user_id, "replacor_strings", [])

    async def set_replacor_strings(self, user_id, strings):
        await self._set(user_id, "replacor_strings", strings)

    async def add_replacor_string(self, user_id, string):
        strings = await self.get_replacor_strings(user_id)
        lower_string = string.strip()
        # Don't add duplicates (case-insensitive)
        if lower_string.lower() not in [s.lower() for s in strings]:
            strings.append(lower_string)
            await self.set_replacor_strings(user_id, strings)
        return strings

    async def remove_replacor_string(self, user_id, string):
        strings = await self.get_replacor_strings(user_id)
        strings = [s for s in strings if s.lower() != string.lower()]
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

# ================= WATERMARK COLOR & STYLE =================
    async def get_watermark_color(self, user_id):
        return await self._get(user_id, "watermark_color", "white")

    async def set_watermark_color(self, user_id, color):
        await self._set(user_id, "watermark_color", color)

    async def get_watermark_style(self, user_id):
        return await self._get(user_id, "watermark_style", "shadow")

    async def set_watermark_style(self, user_id, style):
        await self._set(user_id, "watermark_style", style)

# ================= WATERMARK APPLY MODE =================
    async def get_watermark_apply(self, user_id):
        return await self._get(user_id, "watermark_apply", "ask")

    async def set_watermark_apply(self, user_id, mode):
        """mode: 'on', 'off', 'ask'"""
        await self._set(user_id, "watermark_apply", mode)



# ================= PER-PROCESS WATERMARK TOGGLE =================
    async def get_wm_process(self, user_id, process):
        """Get watermark toggle for a specific process: 'on', 'off', 'ask'."""
        toggles = await self._get(user_id, "wm_per_process", {})
        if isinstance(toggles, dict):
            return toggles.get(process, "ask")
        return "ask"

    async def set_wm_process(self, user_id, process, state):
        toggles = await self._get(user_id, "wm_per_process", {})
        if not isinstance(toggles, dict): toggles = {}
        toggles[process] = state
        await self._set(user_id, "wm_per_process", toggles)

    async def get_all_wm_processes(self, user_id):
        toggles = await self._get(user_id, "wm_per_process", {})
        if not isinstance(toggles, dict): toggles = {}
        defaults = {"encode":"ask","compress":"ask","merge":"ask","rename":"ask","autorename":"off","upscale":"ask"}
        for k,v in defaults.items():
            if k not in toggles: toggles[k] = v
        return toggles

# ================= PER-PROCESS AUDIO REORDER (AF) TOGGLE =================
    async def get_af_process(self, user_id, process):
        """Get audio reorder toggle for a specific process: 'on', 'off', 'ask'."""
        toggles = await self._get(user_id, "af_per_process", {})
        if isinstance(toggles, dict):
            return toggles.get(process, "ask")
        return "ask"

    async def set_af_process(self, user_id, process, state):
        toggles = await self._get(user_id, "af_per_process", {})
        if not isinstance(toggles, dict): toggles = {}
        toggles[process] = state
        await self._set(user_id, "af_per_process", toggles)

    async def get_all_af_processes(self, user_id):
        toggles = await self._get(user_id, "af_per_process", {})
        if not isinstance(toggles, dict): toggles = {}
        defaults = {"encode":"ask","compress":"ask","merge":"off","rename":"off","autorename":"off"}
        for k,v in defaults.items():
            if k not in toggles: toggles[k] = v
        return toggles


codeflixbots = Database(Config.DB_URL, Config.DB_NAME)
