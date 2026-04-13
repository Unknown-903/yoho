import time

auth_chats = set()

# ================= USER AUTH WITH EXPIRY =================
# user_id -> expiry_timestamp (None = unlimited)
auth_users = {}


def add_auth_user(user_id, days=None):
    """Authorize a user. days=None means unlimited."""
    user_id = int(user_id)
    if days and days > 0:
        auth_users[user_id] = time.time() + (days * 86400)
    else:
        auth_users[user_id] = None


def remove_auth_user(user_id):
    """Remove user authorization."""
    auth_users.pop(int(user_id), None)


def is_auth_user(user_id):
    """Check if a user is authorized (and not expired)."""
    user_id = int(user_id)
    if user_id not in auth_users:
        return False
    expiry = auth_users[user_id]
    if expiry is None:
        return True
    if time.time() > expiry:
        auth_users.pop(user_id, None)
        return False
    return True


def get_auth_remaining(user_id):
    """
    Returns remaining days as float, None for unlimited, -1 if not authorized.
    """
    user_id = int(user_id)
    if user_id not in auth_users:
        return -1
    expiry = auth_users[user_id]
    if expiry is None:
        return None
    remaining = expiry - time.time()
    if remaining <= 0:
        auth_users.pop(user_id, None)
        return -1
    return remaining / 86400


def get_all_auth_users():
    """Return dict of {user_id: expiry_or_none}, cleaning expired ones."""
    now = time.time()
    expired = [uid for uid, exp in auth_users.items() if exp is not None and now > exp]
    for uid in expired:
        auth_users.pop(uid, None)
    return dict(auth_users)
