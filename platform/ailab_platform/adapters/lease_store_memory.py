"""In-process LeaseStore for tests and an explicitly single-process development Broker."""

import copy
import threading
import time

from ..ports.lease_store import LIVE_STATES


class MemoryLeaseStore:
    def __init__(self) -> None:
        self._records: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._owner: tuple[str, float] | None = None

    def get(self, lease_id: str) -> dict | None:
        with self._lock:
            return copy.deepcopy(self._records.get(lease_id))

    def all(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(list(self._records.values()))

    def live(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy([x for x in self._records.values() if x["state"] in LIVE_STATES])

    def cas(self, lease_id: str, version: int | None, record: dict) -> bool:
        with self._lock:
            current = self._records.get(lease_id)
            if (current or {}).get("version") != version:
                return False
            self._records[lease_id] = copy.deepcopy({**record, "version": (version or 0) + 1})
            return True

    def delete(self, lease_id: str, version: int) -> bool:
        with self._lock:
            if (self._records.get(lease_id) or {}).get("version") != version:
                return False
            del self._records[lease_id]
            return True

    def claim_owner(self, token: str, ttl: int) -> bool:
        with self._lock:
            now = time.monotonic()
            if self._owner and self._owner[0] != token and self._owner[1] > now:
                return False
            self._owner = (token, now + ttl)
            return True

    def owns(self, token: str) -> bool:
        with self._lock:
            return bool(self._owner and self._owner[0] == token and self._owner[1] > time.monotonic())

    def release_owner(self, token: str) -> None:
        with self._lock:
            if self._owner and self._owner[0] == token:
                self._owner = None
