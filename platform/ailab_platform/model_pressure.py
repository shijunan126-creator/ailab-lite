"""Five-minute gateway cooling signal shared by Agent and API without business DB access."""

import time
from functools import lru_cache

from .config import settings

_until = 0.0
_KEY = "ailab:model:memory-cooldown"


@lru_cache(maxsize=1)
def _redis():
    import redis

    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.25, socket_timeout=0.25)


def record():
    global _until
    _until = time.monotonic() + 300
    if settings.queue_backend == "redis":
        try:
            _redis().set(_KEY, "1", ex=300)
        except Exception:
            pass  # local cooling still protects this process; never break an interactive call


def active():
    if time.monotonic() < _until:
        return True
    if settings.queue_backend == "redis":
        try:
            return bool(_redis().exists(_KEY))
        except Exception:
            return True  # inability to coordinate must not increase background pressure
    return False
