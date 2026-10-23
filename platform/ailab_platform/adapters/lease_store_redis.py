"""Redis transactions protect desired state, including after a process restart.

No TTL on live records: provider delete failures must retain ownership indefinitely.
Terminal records are pruned by reconciliation after the diagnostic retention window.
"""

import json


class RedisLeaseStore:
    def __init__(self, client, owner: str) -> None:
        self.client = client
        self.key = f"ailab:broker:{{{owner}}}:leases"
        self.live_key = f"ailab:broker:{{{owner}}}:live"
        self.owner_key = f"ailab:broker:{{{owner}}}:owner"

    def get(self, lease_id: str) -> dict | None:
        raw = self.client.hget(self.key, lease_id)
        return json.loads(raw) if raw else None

    def all(self) -> list[dict]:
        return [json.loads(raw) for _, raw in self.client.hscan_iter(self.key, count=100)]

    def live(self) -> list[dict]:
        ids = self.client.smembers(self.live_key)
        if not ids:
            return []
        return [json.loads(raw) for raw in self.client.hmget(self.key, list(ids)) if raw]

    def cas(self, lease_id: str, version: int | None, record: dict) -> bool:
        payload = json.dumps({**record, "version": (version or 0) + 1}, ensure_ascii=False)
        # Compare just this field, not WATCH on the whole hash: unrelated leases
        # may renew concurrently without causing spurious state conflicts.
        return bool(
            self.client.eval(
                "local raw=redis.call('HGET',KEYS[1],ARGV[1]); local v=0; "
                "if raw then v=cjson.decode(raw).version end; "
                "if v~=tonumber(ARGV[2]) then return 0 end; "
                "redis.call('HSET',KEYS[1],ARGV[1],ARGV[3]); "
                "local state=cjson.decode(ARGV[3]).state; "
                "if state=='creating' or state=='active' or state=='releasing' then "
                "redis.call('SADD',KEYS[2],ARGV[1]) else redis.call('SREM',KEYS[2],ARGV[1]) end; return 1",
                2,
                self.key,
                self.live_key,
                lease_id,
                version or 0,
                payload,
            )
        )

    def delete(self, lease_id: str, version: int) -> bool:
        return bool(
            self.client.eval(
                "local raw=redis.call('HGET',KEYS[1],ARGV[1]); "
                "if raw and cjson.decode(raw).version==tonumber(ARGV[2]) then "
                "return redis.call('HDEL',KEYS[1],ARGV[1]) end; return 0",
                1,
                self.key,
                lease_id,
                version,
            )
        )

    def claim_owner(self, token: str, ttl: int) -> bool:
        return bool(
            self.client.eval(
                "local v=redis.call('GET',KEYS[1]); "
                "if not v or v==ARGV[1] then redis.call('SET',KEYS[1],ARGV[1],'EX',ARGV[2]); return 1 end; return 0",
                1,
                self.owner_key,
                token,
                ttl,
            )
        )

    def owns(self, token: str) -> bool:
        value = self.client.get(self.owner_key)
        return value == token or value == token.encode()

    def release_owner(self, token: str) -> None:
        self.client.eval(
            "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) end; return 0",
            1,
            self.owner_key,
            token,
        )
