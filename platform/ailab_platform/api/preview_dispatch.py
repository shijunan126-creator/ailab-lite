"""Bounded publication with retry backoff; the database retains jobs until consumer ACK."""

import asyncio
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update

from ..config import settings
from ..contracts.jobs import JobEnvelope
from ..db import SessionLocal
from ..models import OutboxMessage
from ..queue import get_queue, job_topic

log = logging.getLogger("ailab.api.jobs")
_dispatch_lock = threading.Lock()


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
        sent = 0
        for message in db.scalars(query):
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
    while True:
        try:
            from .file_dependencies import reconcile

            await asyncio.to_thread(reconcile)
            await asyncio.to_thread(dispatch_preview_jobs)
        except Exception:
            log.exception("outbox dispatch failed")
        await asyncio.sleep(1)
