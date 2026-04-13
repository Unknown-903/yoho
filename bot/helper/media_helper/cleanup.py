"""Centralized Cleanup."""
import os,asyncio,shutil,logging
logger=logging.getLogger(__name__)
async def cleanup_task(client=None,chat_id=None,msg_ids=None,file_paths=None,dir_paths=None,delay=1.5):
    if delay>0:await asyncio.sleep(delay)
    for mid in(msg_ids or[]):
        try:await client.delete_messages(chat_id,mid)if mid else None
        except:pass
    for fp in(file_paths or[]):
        try:os.remove(fp)if fp and os.path.exists(fp)else None
        except:pass
    for dp in(dir_paths or[]):
        try:shutil.rmtree(dp,ignore_errors=True)if dp and os.path.exists(dp)else None
        except:pass
async def safe_delete_message(c,cid,mid):
    try:await c.delete_messages(cid,mid)if c and cid and mid else None
    except:pass
async def safe_delete_files(*paths):
    for p in paths:
        try:
            if p and os.path.exists(p):shutil.rmtree(p,True)if os.path.isdir(p)else os.remove(p)
        except:pass
def cleanup_user_dir(uid,dirs=None):
    for b in(dirs or["downloads/","encodes/","compressed/","merged/"]):
        d=os.path.join(b,str(uid))
        if os.path.exists(d):shutil.rmtree(d,True)
