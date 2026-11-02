"""Ephemeral view snapshots: Redis in multi-process profiles, bounded memory in tests."""

import json
import threading
import time
from collections import OrderedDict

from ..config import settings

TTL = 600
MAX_VIEWS = 16
_memory = OrderedDict()
_lock = threading.Lock()


def _key(user_id, conversation_id):
    return f"ailab:views:{user_id}:{conversation_id}"


def _redis():
    import redis

    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)


def views(user_id, conversation_id):
    key = _key(user_id, conversation_id)
    now = time.time()
    if settings.queue_backend == "memory":
        with _lock:
            entries = _memory.get(key, {})
            entries = {k: v for k, v in entries.items() if v[0] > now}
            if entries:
                _memory[key] = entries
            else:
                _memory.pop(key, None)
            return [v[1] for v in entries.values()]
    with _redis() as client:
        return [json.loads(v)[1] for v in client.hvals(key) if json.loads(v)[0] > now]


def put(user_id, conversation_id, state):
    key = _key(user_id, conversation_id)
    now = time.time()
    if settings.queue_backend == "memory":
        with _lock:
            entries = _memory.setdefault(key, {})
            for view_id in list(entries):
                if entries[view_id][0] <= now:
                    del entries[view_id]
            if state["view_id"] not in entries and len(entries) >= MAX_VIEWS:
                raise ValueError("too many views")
            entries[state["view_id"]] = (now + TTL, state)
            _memory.move_to_end(key)
            while len(_memory) > 4096:
                _memory.popitem(last=False)
        return
    with _redis() as client:
        # Bound view count atomically across API workers.
        accepted = client.eval(
            "for _,k in ipairs(redis.call('HKEYS',KEYS[1])) do "
            "local v=cjson.decode(redis.call('HGET',KEYS[1],k)); if v[1]<=tonumber(ARGV[3]) then redis.call('HDEL',KEYS[1],k) end end; "
            "local old=redis.call('HEXISTS',KEYS[1],ARGV[1]); "
            "if old==0 and redis.call('HLEN',KEYS[1])>=16 then return 0 end; "
            "redis.call('HSET',KEYS[1],ARGV[1],ARGV[2]); redis.call('EXPIRE',KEYS[1],600); return 1",
            1,
            key,
            state["view_id"],
            json.dumps([now + TTL, state]),
            now,
        )
        if not accepted:
            raise ValueError("too many views")


def delete(user_id, conversation_id, view_id):
    key = _key(user_id, conversation_id)
    if settings.queue_backend == "memory":
        with _lock:
            _memory.get(key, {}).pop(view_id, None)
    else:
        with _redis() as client:
            client.hdel(key, view_id)
