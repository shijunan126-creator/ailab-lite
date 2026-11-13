"""Bounded publication with retry backoff; the database retains jobs until consumer ACK."""

import asyncio
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update

from ..config import settings
from ..contracts.jobs import JobEnvelope
from ..db import SessionLocal
from ..models import FileObject, OutboxMessage
from ..queue import get_queue, job_topic

log = logging.getLogger("ailab.api.jobs")
_dispatch_lock = threading.Lock()
# Idle cadence. A committed job wakes the loop instead of waiting for the next tick; this interval remains the
# fallback when the wake is missed, comes from another process, or the loop never saw it.
OUTBOX_POLL_SECONDS = 1.0
_serving_loop: asyncio.AbstractEventLoop | None = None
_dispatch_wakeup: asyncio.Event | None = None


def wake_dispatch() -> None:
    """Ask the running dispatcher to read the outbox now; safe from any thread after a commit.

    Only shortens the wait before publication. Without a bound loop (startup, shutdown, a non-API process, a test
    that drives dispatch itself) this does nothing and the poll remains the delivery path.
    """
    loop, wakeup = _serving_loop, _dispatch_wakeup
    if loop is None or wakeup is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(wakeup.set)
    except RuntimeError:
        pass  # the loop closed between the check and the call (shutdown)


def _fair_preview_order(messages, owners):
    """Round-robin due preview jobs per user within priority, retaining other job positions."""
    previews = [message for message in messages if message.payload.get("kind") == "preview"]
    groups = defaultdict(deque)
    for message in previews:
        groups[(bool(message.payload.get("priority")), owners.get(message.payload.get("file_id"), message.id))].append(message)
    ordered = []
    for priority in (True, False):
        users = deque(key for key in groups if key[0] == priority)
        while users:
            key = users.popleft()
            ordered.append(groups[key].popleft())
            if groups[key]:
                users.append(key)
    preview_iter = iter(ordered)
    return [next(preview_iter) if message.payload.get("kind") == "preview" else message for message in messages]


def dispatch_preview_jobs(job_id: str | None = None, *, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    queue = get_queue()
    # The lock is an optimization, never a prerequisite to recording Redis failures.
    token = None
    if job_id is None:
        try:
            token = queue.acquire_lock("ailab:lock:outbox-dispatch", 60)
            if token is None:
                return 0
        except Exception:
            pass
    try:
        return _dispatch_batch(queue, job_id, now)
    finally:
        if token:
            try:
                queue.release_lock("ailab:lock:outbox-dispatch", token)
            except Exception:
                log.warning("outbox leader release unavailable")


def _dispatch_batch(queue, job_id, now):
    deadline = time.monotonic() + 5
    with _dispatch_lock, SessionLocal() as db:
        query = select(OutboxMessage).where(
            OutboxMessage.needs_attention == 0,
            OutboxMessage.next_attempt_at <= now,
            or_(OutboxMessage.claimed_until.is_(None), OutboxMessage.claimed_until <= now),
        )
        if job_id:
            query = query.where(OutboxMessage.id == job_id)
        query = query.order_by(OutboxMessage.next_attempt_at, OutboxMessage.id).limit(50)
        if db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        messages = list(db.scalars(query))
        file_ids = {message.payload.get("file_id") for message in messages if message.payload.get("kind") == "preview"}
        owners = dict(db.execute(select(FileObject.id, FileObject.user_id).where(FileObject.id.in_(file_ids))).all()) if file_ids else {}
        messages = _fair_preview_order(messages, owners)
        sent = 0
        for message in messages:
            if time.monotonic() >= deadline:
                break
            observed_at, observed_token = message.next_attempt_at, message.claim_token
            values = {}
            payload = message.payload
            # 0011 preview rows are upgraded at publication, keeping their stable identity.
            if "schema_version" not in payload:
                payload = JobEnvelope(
                    job_id=message.id,
                    kind="preview",
                    file_id=message.payload["file_id"],
                    deadline=now + timedelta(seconds=settings.job_deadline_seconds),
                ).model_dump(mode="json")
                values["payload"] = payload
            try:
                envelope = JobEnvelope.model_validate(payload)
                topic = job_topic(envelope.kind, envelope.mode, envelope.priority)
                queue.enqueue(topic, payload)
            except Exception as exc:
                attempts = message.attempts + 1
                values.update(
                    attempts=attempts,
                    last_error=type(exc).__name__[:120],
                    next_attempt_at=now + timedelta(seconds=min(300, 2 ** min(attempts, 9))),
                    needs_attention=int(attempts >= settings.outbox_max_attempts),
                )
                if values["needs_attention"]:
                    log.error("outbox publication exhausted; manual retry required")
            else:
                values.update(attempts=0, last_error=None, next_attempt_at=now + timedelta(seconds=60))
                sent += 1
            # SQLite SELECT may not hold a write lock. Never overwrite a concurrent
            # consumer claim or release using the dispatcher's earlier snapshot.
            db.execute(
                update(OutboxMessage)
                .where(
                    OutboxMessage.id == message.id,
                    OutboxMessage.next_attempt_at == observed_at,
                    OutboxMessage.claim_token == observed_token,
                    or_(OutboxMessage.claimed_until.is_(None), OutboxMessage.claimed_until <= now),
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
        db.commit()
        return sent


async def dispatch_forever() -> None:
    global _serving_loop, _dispatch_wakeup
    loop = asyncio.get_running_loop()
    wakeup = asyncio.Event()
    _serving_loop, _dispatch_wakeup = loop, wakeup
    next_reconcile = 0.0
    try:
        while True:
            # Cleared before dispatching: a commit during this pass leaves the event set and runs another pass
            # instead of waiting out the poll interval.
            wakeup.clear()
            try:
                now = time.monotonic()
                if now >= next_reconcile:
                    next_reconcile = now + OUTBOX_POLL_SECONDS
                    from .file_dependencies import reconcile

                    await asyncio.to_thread(reconcile)
                await asyncio.to_thread(dispatch_preview_jobs)
            except Exception:
                log.exception("outbox dispatch failed")
            try:
                await asyncio.wait_for(wakeup.wait(), OUTBOX_POLL_SECONDS)
            except TimeoutError:
                pass
    finally:
        if _dispatch_wakeup is wakeup:
            _dispatch_wakeup = None
        if _serving_loop is loop:
            _serving_loop = None
