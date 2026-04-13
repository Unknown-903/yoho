"""Shim: media_tools still import task_manager from here.
Delegates to Aeon's global task_dict via a lightweight wrapper.
"""
import time
import logging
from pytz import timezone

logger = logging.getLogger(__name__)
IST = timezone("Asia/Kolkata")

def _fe(s):
    s = int(s)
    if s < 60: return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60: return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


class _MediaTaskManager:
    """Lightweight task registry for media processing tasks.
    Works alongside Aeon's task_dict — media tasks register here
    AND in Aeon's task_dict (via MediaStatus) so /status shows them.
    """
    def __init__(self):
        self._t = {}

    def register(self, task_id, user_id, command, file_name="",
                 username="", chat_id=0, msg_id=0):
        self._t[task_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "username": username,
            "command": command,
            "file_name": file_name,
            "status": "queued",
            "progress": 0,
            "started": time.time(),
            "chat_id": chat_id,
            "error": "",
        }

    def update_progress(self, task_id, progress=None, status=None):
        t = self._t.get(task_id)
        if not t: return
        if progress is not None: t["progress"] = progress
        if status   is not None: t["status"]   = status

    def set_error(self, task_id, error_msg=""):
        t = self._t.get(task_id)
        if t:
            t["status"] = "error"
            t["error"]  = error_msg

    def complete(self, task_id):
        self._t.pop(task_id, None)

    def get_all_active(self):
        return sorted(self._t.values(), key=lambda x: x["started"])

    def get_user_tasks(self, user_id):
        return [t for t in self._t.values() if t["user_id"] == int(user_id)]

    def count_active(self):
        return len(self._t)

    def cleanup_stale(self, hrs=6):
        cut = time.time() - hrs * 3600
        stale = [tid for tid, t in self._t.items() if t["started"] < cut]
        for tid in stale:
            self._t.pop(tid, None)
        return len(stale)


task_manager = _MediaTaskManager()
