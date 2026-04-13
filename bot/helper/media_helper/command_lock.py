"""Per-User Command Lock."""
import time,logging
logger=logging.getLogger(__name__)
_locks={}
def acquire_lock(uid,cmd,task_id="",file_name=""):
    uid,cmd=int(uid),cmd.lower().strip()
    if uid not in _locks:_locks[uid]={}
    if cmd in _locks[uid]:return False
    _locks[uid][cmd]={"task_id":task_id,"started":time.time(),"file_name":file_name}
    return True
def release_lock(uid,cmd):
    uid,cmd=int(uid),cmd.lower().strip()
    if uid in _locks:
        _locks[uid].pop(cmd,None)
        if not _locks[uid]:del _locks[uid]
def is_locked(uid,cmd):return int(uid) in _locks and cmd.lower().strip() in _locks.get(int(uid),{})
def get_all_locks():return{u:dict(c)for u,c in _locks.items()}
def force_release_all(uid):_locks.pop(int(uid),None)
def get_lock_info(uid,cmd):
    uid,cmd=int(uid),cmd.lower().strip()
    if uid in _locks and cmd in _locks[uid]:
        i=_locks[uid][cmd];return{**i,"elapsed":time.time()-i["started"]}
    return None
