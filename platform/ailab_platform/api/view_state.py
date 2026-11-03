"""Bounded ephemeral snapshots with atomic report/close fencing, shared by API workers."""

import json
import threading
import time
from collections import OrderedDict
from uuid import uuid4

from ..config import settings

TTL = 600
WRITE_SECONDS = 120  # Longer than workspace validation's 90 s deadline; commits after this are rejected.
MAX_VIEWS = 16
MAX_RECORDS = 128  # Includes pending reports and closed generations; never evict a live fence for a new view.
_memory = OrderedDict()
_lock = threading.Lock()


def _key(user_id, conversation_id):
    return f"ailab:views:{user_id}:{conversation_id}"


def _redis():
    import redis

    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)


def _record(value):
    # Read pre-upgrade snapshots until their existing ten-minute expiry.
    if isinstance(value, (list, tuple)):
        return {"until": value[0], "expires": value[0], "state": value[1], "seq": 0}
    return value


# Each mutation is one script (or one memory lock). Reserving before expensive
# authorization prevents its completion from overwriting a newer report or close.
_SCRIPT = """
local now=tonumber(ARGV[1]); local id=ARGV[2]; local action=ARGV[3]
local ttl=tonumber(ARGV[4]); local maxviews=tonumber(ARGV[5]); local maxrecords=tonumber(ARGV[6])
local records={}; local count=0; local active=0
for _,k in ipairs(redis.call('HKEYS',KEYS[1])) do
  local v=cjson.decode(redis.call('HGET',KEYS[1],k))
  if v[1] then v={until_=v[1],expires=v[1],state=v[2],seq=0}; v['until']=v.until_ end
  if v['until']<=now then redis.call('HDEL',KEYS[1],k)
  else
    if v.state and (v.expires or 0)<=now then
      v.state=nil; v.expires=0; redis.call('HSET',KEYS[1],k,cjson.encode(v))
    end
    records[k]=v; count=count+1
    if not v.closed and ((v.expires or 0)>now or (v.deadline or 0)>now) then active=active+1 end
  end
end
local v=records[id]
if action=='begin' then
  if v and v.closed then return 0 end
  local seq=tonumber(ARGV[7]); if seq==0 then seq=(v and v.seq or 0)+1 end
  if v and seq<=v.seq then return 0 end
  if not v and count>=maxrecords then return -1 end
  local live=v and ((v.expires or 0)>now or (v.deadline or 0)>now)
  if not live and active>=maxviews then return -1 end
  v=v or {}; v.seq=seq; v.token=ARGV[8]; v.deadline=now+tonumber(ARGV[9]); v['until']=now+ttl
elseif action=='commit' then
  if not v or v.closed or v.token~=ARGV[8] or v.deadline<=now then return 0 end
  v.state=cjson.decode(ARGV[10]); v.expires=now+ttl; v['until']=now+ttl; v.deadline=0
elseif action=='close' then
  if not v and count>=maxrecords then return -1 end
  v={closed=true,seq=v and v.seq or 0}; v['until']=now+ttl
end
redis.call('HSET',KEYS[1],id,cjson.encode(v)); redis.call('EXPIRE',KEYS[1],ttl); return 1
"""


def _mutate(user_id, conversation_id, view_id, action, *, sequence=0, token="", state=None):
    key, now = _key(user_id, conversation_id), time.time()
    if settings.queue_backend != "memory":
        with _redis() as client:
            result = client.eval(
                _SCRIPT, 1, key, now, view_id, action, TTL, MAX_VIEWS, MAX_RECORDS, sequence, token, WRITE_SECONDS, json.dumps(state)
            )
        if result == -1:
            raise ValueError("too many views")
        return bool(result)
    with _lock:
        entries = _memory.setdefault(key, {})
        for ident in list(entries):
            record = _record(entries[ident])
            if record["until"] <= now:
                del entries[ident]
            else:
                if record.get("expires", 0) <= now:
                    record.pop("state", None)
                    record["expires"] = 0
                entries[ident] = record
        prior = entries.get(view_id)
        if action == "begin":
            if prior and prior.get("closed"):
                return False
            sequence = sequence or (prior or {}).get("seq", 0) + 1
            if prior and sequence <= prior["seq"]:
                return False
            if not prior and len(entries) >= MAX_RECORDS:
                raise ValueError("too many views")

            def active(record):
                return not record.get("closed") and max(record.get("expires", 0), record.get("deadline", 0)) > now

            if not (prior and active(prior)) and sum(active(record) for record in entries.values()) >= MAX_VIEWS:
                raise ValueError("too many views")
            entries[view_id] = {**(prior or {}), "seq": sequence, "token": token, "deadline": now + WRITE_SECONDS, "until": now + TTL}
        elif action == "commit":
            if not prior or prior.get("closed") or prior.get("token") != token or prior.get("deadline", 0) <= now:
                return False
            entries[view_id] = {**prior, "state": state, "expires": now + TTL, "until": now + TTL, "deadline": 0}
        elif action == "close":
            if not prior and len(entries) >= MAX_RECORDS:
                raise ValueError("too many views")
            entries[view_id] = {"closed": True, "seq": (prior or {}).get("seq", 0), "until": now + TTL}
        _memory.move_to_end(key)
        while len(_memory) > 4096:
            _memory.popitem(last=False)  # A missing reservation cannot commit, even after eviction.
        return True


def begin(user_id, conversation_id, view_id, sequence=None):
    token = uuid4().hex
    return token if _mutate(user_id, conversation_id, view_id, "begin", sequence=sequence or 0, token=token) else None


def commit(user_id, conversation_id, state, token):
    return _mutate(user_id, conversation_id, state["view_id"], "commit", token=token, state=state)


def views(user_id, conversation_id):
    key, now = _key(user_id, conversation_id), time.time()
    if settings.queue_backend == "memory":
        with _lock:
            records = [dict(_record(value)) for value in _memory.get(key, {}).values()]
    else:
        with _redis() as client:
            records = [json.loads(value) for value in client.hvals(key)]
    return [v["state"] for item in records if not (v := _record(item)).get("closed") and v.get("expires", 0) > now]


def delete(user_id, conversation_id, view_id):
    _mutate(user_id, conversation_id, view_id, "close")
