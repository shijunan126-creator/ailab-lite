"""Global lease admission and deterministic staggering; never holds a lock over model I/O."""

import hashlib
from contextlib import contextmanager

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..config import settings
from ..models import MemoryDreamRun, PlatformSetting, Run, RunStatus


def offset(user_id, minutes):
    return int.from_bytes(hashlib.sha256(user_id.encode()).digest()[:8], "big") % max(1, minutes)


@contextmanager
def admission(db):
    # Upsert establishes a cross-process write lock even on SQLite; on PG the
    # conflict UPDATE locks the singleton row. Caller commits the claim atomically.
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    db.execute(
        insert(PlatformSetting)
        .values(key="memory_scheduler", value={})
        .on_conflict_do_update(index_elements=[PlatformSetting.key], set_={"key": "memory_scheduler"})
    )
    row = db.scalar(select(PlatformSetting).where(PlatformSetting.key == "memory_scheduler").execution_options(populate_existing=True))
    yield row


def can_start(db, row, job, now):
    leased = db.scalar(select(func.count()).select_from(MemoryDreamRun).where(MemoryDreamRun.claimed_until > now))
    same_user = db.scalar(
        select(MemoryDreamRun.id)
        .where(
            MemoryDreamRun.user_id == job.user_id,
            MemoryDreamRun.id != job.id,
            or_(MemoryDreamRun.claimed_until > now, MemoryDreamRun.report["chain_running"].as_boolean().is_(True)),
        )
        .limit(1)
    )
    if same_user or leased >= settings.memory_dream_concurrency:
        return False
    from ..model_pressure import active

    if job.trigger != "cron":
        return True
    running = db.scalar(select(func.count()).select_from(Run).where(Run.status == RunStatus.RUNNING))
    values = row.value or {}
    from ..memory_recall.store import stamp

    cooldown = stamp(values.get("cooldown"))
    if running > settings.memory_dream_busy_runs or (cooldown and cooldown > now) or active():
        return False
    minute = now.isoformat()[:16]
    return values.get("minute") != minute or values.get("tokens", 0) < settings.memory_dream_tokens_per_minute


def account_tokens(db, usage, kind, now):
    from ..metrics import MEMORY_DREAM_TOKENS

    count = max(0, int(usage.get("prompt_tokens", 0))) + max(0, int(usage.get("completion_tokens", 0)))
    with admission(db) as row:
        value = dict(row.value or {})
        minute = now.isoformat()[:16]
        value["tokens"] = (value.get("tokens", 0) if value.get("minute") == minute else 0) + count
        value["minute"] = minute
        row.value = value
        db.commit()
    MEMORY_DREAM_TOKENS.labels(kind if kind in {"extract", "dream", "edit", "merge"} else "extract").inc(count)
