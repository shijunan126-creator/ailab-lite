"""Global lease admission and deterministic staggering; never holds a lock over model I/O."""

import hashlib
import threading
from datetime import timedelta
from contextlib import contextmanager

from sqlalchemy import select, func

from ..config import settings
from ..models import MemoryDreamRun, PlatformSetting, Run, RunStatus, utcnow

_guard = threading.Lock()


def offset(user_id, minutes):
    return int.from_bytes(hashlib.sha256(user_id.encode()).digest()[:8], "big") % max(1, minutes)


@contextmanager
def admission(db):
    with _guard:
        row = db.scalar(select(PlatformSetting).where(PlatformSetting.key == "memory_scheduler").with_for_update())
        if row is None:
            row = PlatformSetting(key="memory_scheduler", value={})
            db.add(row)
            db.flush()
        yield row


def can_start(db, row, job, now):
    leased = db.scalar(select(func.count()).select_from(MemoryDreamRun).where(MemoryDreamRun.claimed_until > now))
    if leased >= settings.memory_dream_concurrency:
        return False
    if job.trigger != "cron":
        return True
    running = db.scalar(select(func.count()).select_from(Run).where(Run.status == RunStatus.RUNNING))
    values = row.value or {}
    from ..memory_recall.store import stamp

    cooldown = stamp(values.get("cooldown"))
    if running > settings.memory_dream_busy_runs or (cooldown and cooldown > now):
        return False
    minute = now.isoformat()[:16]
    return values.get("minute") != minute or values.get("tokens", 0) < settings.memory_dream_tokens_per_minute
