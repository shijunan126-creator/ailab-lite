"""Background repair of work the queue lost track of.

The queue redelivers messages whose consumer died. This covers what the queue
itself cannot: a row committed but never enqueued (API crashed in between, or
the enqueue failed), and a message wiped from the queue (Redis restarted without
persistence) while its run was still marked running.

Single-instance: with several API replicas it needs leader election (or a
lock), otherwise replicas would race to requeue the same rows.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import metrics
from ..config import settings
from ..db import SessionLocal
from ..events import append_event
from ..models import FileObject, FileStatus, Run, RunStatus
from ..queue import JobQueue, get_queue, run_lock_key
from ..quota import expire_reservations
from ..run_state import transition_run
from ..storage import get_storage

log = logging.getLogger("ailab.api.reaper")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:  # SQLite drops tzinfo
        return dt.replace(tzinfo=UTC)
    return dt


def reap_once(db: Session, queue: JobQueue, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(seconds=settings.reaper_grace_seconds)
    stats = {"runs_requeued": 0, "runs_failed": 0, "files_requeued": 0}
    expire_reservations(db, now)

    queued_runs = {p.get("run_id") for p in queue.outstanding(settings.run_queue)}
    for run in db.scalars(select(Run).where(Run.status.in_(RunStatus.ACTIVE))):
        if run.id in queued_runs:
            continue
        if run.status == RunStatus.QUEUED:
            if _aware(run.created_at) > cutoff:
                continue
            queue.enqueue(settings.run_queue, {"run_id": run.id})
            append_event(db, run.id, "run.requeued", {"reason": "no queue message found"})
            stats["runs_requeued"] += 1
            metrics.REAPER_ACTIONS.labels("run_requeued").inc()
            log.warning("requeued run %s: it was queued but had no queue message", run.id)
        else:  # running
            if queue.is_locked(run_lock_key(run.id)):
                continue  # an agent is actively executing it
            started = _aware(run.started_at) or _aware(run.created_at)
            if started > cutoff:
                continue
            _, changed = transition_run(
                db,
                run.id,
                RunStatus.FAILED,
                error="agent lost: the run was executing but no agent or queue message remains",
            )
            stats["runs_failed"] += int(changed)
            if changed:
                metrics.REAPER_ACTIONS.labels("run_failed").inc()
            log.warning("failed run %s: running with no agent and no queue message", run.id)

    queued_files = {p.get("file_id") for p in queue.outstanding(settings.file_queue)}
    for file in db.scalars(select(FileObject).where(FileObject.status == FileStatus.PENDING)):
        if (file.meta or {}).get("processing") != "queued" or file.id in queued_files:
            continue
        if _aware(file.created_at) > cutoff:
            continue
        queue.enqueue(settings.file_queue, {"file_id": file.id})
        stats["files_requeued"] += 1
        metrics.REAPER_ACTIONS.labels("file_requeued").inc()
        log.warning("requeued file %s: pending scan had no queue message", file.id)
    return stats


REAPER_LOCK = "ailab:lock:reaper"
WORKSPACE_GC_LOCK = "ailab:lock:workspace-gc"


def purge_expired_workspaces(storage=None, now: datetime | None = None) -> int:
    """Delete workspace snapshots whose object-store timestamp is older than the TTL.

    The TTL is enforced on the object store itself (S3 LastModified / local mtime),
    so a missing or stale database flag cannot keep a snapshot alive forever.
    """
    if settings.workspace_ttl_days <= 0:  # 0 = keep snapshots (until a newer turn replaces them)
        return 0
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=settings.workspace_ttl_days)
    storage = storage or get_storage()
    removed = 0
    for item in _page(storage, "workspaces/"):
        modified = item.modified_at
        if modified.tzinfo is None:  # a custom backend may return a naive timestamp
            modified = modified.replace(tzinfo=UTC)
        if modified >= cutoff:
            continue
        try:
            storage.delete(item.key)
        except Exception:
            log.warning("could not delete expired workspace %s", item.key, exc_info=True)
            continue
        removed += 1
    if removed:
        GC_ACTIONS.labels("workspace").inc(removed)
        metrics.WORKSPACE_SNAPSHOTS_EXPIRED.inc(removed)
        log.info("deleted %d workspace snapshot(s) older than %d days", removed, settings.workspace_ttl_days)
    return removed


def _workspace_tick() -> int:
    interval = max(60, settings.workspace_cleanup_interval_seconds)
    queue = get_queue()
    # Several API replicas may run the janitor; one pass per interval is enough.
    if queue.acquire_lock(WORKSPACE_GC_LOCK, max(1, int(interval * 0.8))) is None:
        return 0
    return purge_expired_workspaces()


async def workspace_cleanup_forever() -> None:
    while True:
        await asyncio.sleep(max(60, settings.workspace_cleanup_interval_seconds))
        try:
            await asyncio.to_thread(_workspace_tick)
        except Exception:
            log.exception("workspace cleanup pass failed")


def reap_guarded(queue: JobQueue, session_factory=SessionLocal) -> dict[str, int] | None:
    """One reaper pass, but only if no other API replica ran one within the last interval.

    The lock is deliberately not released: it expires on its own, so a replica that wakes up a moment
    later skips this round instead of repeating it. Several replicas thus share one pass per interval.
    """
    if queue.acquire_lock(REAPER_LOCK, max(1, int(settings.reaper_interval_seconds * 0.8))) is None:
        return None
    with session_factory() as db:
        return reap_once(db, queue)


def _tick() -> dict[str, int] | None:
    return reap_guarded(get_queue())


async def reap_forever() -> None:
    while True:
        await asyncio.sleep(settings.reaper_interval_seconds)
        try:
            await asyncio.to_thread(_tick)
        except Exception:
            log.exception("reaper pass failed")


# Each label belongs to this fixed set; never label metrics with object/user/run ids.
GC_ACTIONS = metrics.Counter("ailab_gc_objects_total", "Lifecycle rows or objects removed", ["kind"])
GC_ERRORS = metrics.Counter("ailab_gc_errors_total", "Lifecycle passes requiring retry", ["kind"])
_gc_cursors: dict[str, str | None] = {}


def _page(storage, prefix: str):
    page = storage.list_prefix(prefix, cursor=_gc_cursors.get(prefix), limit=settings.gc_batch_size)
    _gc_cursors[prefix] = page.cursor
    return page


def _schedule_delete(db, key: str, kind: str, *, prefix=False):
    from ..models import LifecycleDeletion

    if db.scalar(select(LifecycleDeletion.id).where(LifecycleDeletion.key == key)) is None:
        db.add(LifecycleDeletion(key=key, kind=kind, is_prefix=int(prefix)))
        db.flush()


def drain_object_deletions(db: Session, storage) -> int:
    from ..models import LifecycleDeletion

    removed = 0
    budget = settings.gc_batch_size
    for job in db.scalars(select(LifecycleDeletion).order_by(LifecycleDeletion.id).limit(settings.gc_batch_size)).all():
        if budget <= 0:
            break
        try:
            if job.is_prefix:
                # One page per intent per tick, so a huge preview tree cannot monopolize the API.
                page = storage.list_prefix(job.key, limit=budget)
                for item in page:
                    storage.delete(item.key)
                budget -= len(page.items)
                if page.cursor:
                    db.commit()
                    continue
            else:
                storage.delete(job.key)
                budget -= 1
            kind = job.kind
            db.delete(job)
            db.commit()
            removed += 1
            GC_ACTIONS.labels(kind).inc()
        except Exception:
            db.rollback()
            GC_ERRORS.labels(job.kind).inc()
            log.warning("object cleanup will retry", exc_info=True)
    return removed


def purge_expired_presigns(db: Session, storage, now: datetime) -> int:
    from ..locks import user_critical_section

    ttl = settings.presign_staging_ttl_seconds
    if not ttl:
        return 0
    # Signed URLs last 900 seconds. Never collect one that can still be completed.
    cutoff = now - timedelta(seconds=max(ttl, 900 + settings.reaper_grace_seconds))
    removed = 0
    for item in _page(storage, "uploads/"):
        parts = item.key.split("/")
        if len(parts) != 4 or _aware(item.modified_at) >= cutoff:
            continue  # final/frozen objects have a separate obj_* path component
        _, user_id, file_id, _ = parts
        with user_critical_section(db, user_id):
            file = db.get(FileObject, file_id, populate_existing=True)
            if file is not None and file.storage_key == item.key:
                if (file.meta or {}).get("processing") != "awaiting_upload" or _aware(file.created_at) >= cutoff:
                    db.rollback()
                    continue
                db.delete(file)
            _schedule_delete(db, item.key, "presign")
            db.commit()
            removed += 1
    # Also collect expired presigns for which no PUT ever arrived.
    candidates = db.scalars(
        select(FileObject)
        .where(
            FileObject.status == FileStatus.PENDING,
            FileObject.created_at < cutoff,
            FileObject.meta["processing"].as_string() == "awaiting_upload",
        )
        .order_by(FileObject.created_at, FileObject.id)
        .limit(settings.gc_batch_size)
    ).all()
    for candidate in candidates:
        with user_critical_section(db, candidate.user_id):
            db.refresh(candidate)
            if (candidate.meta or {}).get("processing") == "awaiting_upload":
                _schedule_delete(db, candidate.storage_key, "presign")
                db.delete(candidate)
                db.commit()
                removed += 1
            else:
                db.rollback()
    return removed


def purge_orphan_objects(db: Session, storage, now: datetime) -> int:
    if not settings.orphan_objects_grace_seconds:
        return 0
    cutoff = now - timedelta(seconds=settings.orphan_objects_grace_seconds)
    removed = 0
    for prefix, owner in (("derived/", FileObject), ("previews/", Run)):
        for item in _page(storage, prefix):
            if _aware(item.modified_at) >= cutoff:
                continue
            owner_id = item.key.split("/")[1]
            if db.get(owner, owner_id) is not None:
                continue
            _schedule_delete(db, item.key, "orphan")
            db.commit()
            removed += 1
    return removed


def purge_run_events(db: Session, now: datetime) -> int:
    from sqlalchemy import delete

    from ..models import RunEvent

    if not settings.run_events_retention_days:
        return 0
    cutoff = now - timedelta(days=settings.run_events_retention_days)
    ids = list(
        db.scalars(
            select(RunEvent.id)
            .join(Run, Run.id == RunEvent.run_id)
            .where(Run.status.in_(RunStatus.TERMINAL), Run.finished_at < cutoff, RunEvent.created_at < cutoff)
            .order_by(RunEvent.created_at, RunEvent.id)
            .limit(settings.gc_batch_size)
        )
    )
    if ids:
        db.execute(delete(RunEvent).where(RunEvent.id.in_(ids)))
        db.commit()
        GC_ACTIONS.labels("events").inc(len(ids))
    return len(ids)


def purge_deleted_conversations(db: Session, now: datetime) -> int:
    from sqlalchemy import delete

    from ..locks import user_critical_section
    from ..models import Artifact, Conversation, RunEvent

    if not settings.conversation_purge_days:
        return 0
    cutoff = now - timedelta(days=settings.conversation_purge_days)
    candidates = db.scalars(
        select(Conversation)
        .where(Conversation.deleted_at < cutoff)
        .order_by(Conversation.deleted_at, Conversation.id)
        .limit(settings.gc_batch_size)
    ).all()
    remaining = settings.gc_batch_size
    removed = 0
    for candidate in candidates:
        with user_critical_section(db, candidate.user_id):
            db.refresh(candidate)
            if not candidate.deleted_at or _aware(candidate.deleted_at) >= cutoff:
                db.rollback()
                continue
            active = db.scalar(select(Run.id).where(Run.conversation_id == candidate.id, Run.status.in_(RunStatus.ACTIVE)).limit(1))
            if active:
                db.rollback()
                continue
            ids = list(db.scalars(select(Run.id).where(Run.conversation_id == candidate.id).order_by(Run.id).limit(remaining)))
            # A single conversation can have millions of events. Prune those in
            # bounded batches before deleting the parent rows.
            event_ids = list(db.scalars(select(RunEvent.id).where(RunEvent.run_id.in_(ids)).order_by(RunEvent.id).limit(remaining)))
            if event_ids:
                db.execute(delete(RunEvent).where(RunEvent.id.in_(event_ids)))
                remaining -= len(event_ids)
                if remaining <= 0:
                    db.commit()
                    break
            ids = ids[:remaining]
            for run_id in ids:
                _schedule_delete(db, f"workspaces/{run_id}.tgz", "conversation")
                _schedule_delete(db, f"previews/{run_id}/", "conversation", prefix=True)
            if ids:
                # the usage ledger stays: what these runs cost still counts
                for model in (RunEvent, Artifact):
                    db.execute(delete(model).where(model.run_id.in_(ids)))
                db.execute(delete(Run).where(Run.id.in_(ids)))
                remaining -= len(ids)
            if db.scalar(select(Run.id).where(Run.conversation_id == candidate.id).limit(1)) is None:
                db.delete(candidate)
                removed += 1
            db.commit()
        if remaining <= 0:
            break
    GC_ACTIONS.labels("conversation").inc(removed)
    return removed


def lifecycle_once(db: Session, storage=None, now: datetime | None = None) -> dict[str, int]:
    storage = storage or get_storage()
    now = now or datetime.now(UTC)
    stats = {}
    for kind, job in (
        ("presign", lambda: purge_expired_presigns(db, storage, now)),
        ("orphan", lambda: purge_orphan_objects(db, storage, now)),
        ("events", lambda: purge_run_events(db, now)),
        ("conversation", lambda: purge_deleted_conversations(db, now)),
        ("cleanup", lambda: drain_object_deletions(db, storage)),
    ):
        try:
            stats[kind] = job()
        except Exception:
            db.rollback()
            GC_ERRORS.labels(kind).inc()
            log.exception("lifecycle %s pass failed; next tick retries", kind)
    return stats


async def lifecycle_cleanup_forever() -> None:
    def tick():
        queue = get_queue()
        token = queue.acquire_lock("ailab:lock:lifecycle-gc", settings.gc_interval_seconds)
        if token is None:
            return
        # Retain the lock until expiry, so API replicas share one pass per interval.
        with SessionLocal() as db:
            lifecycle_once(db)

    while True:
        await asyncio.sleep(settings.gc_interval_seconds)
        try:
            await asyncio.to_thread(tick)
        except Exception:
            log.exception("lifecycle tick failed; next interval retries")
