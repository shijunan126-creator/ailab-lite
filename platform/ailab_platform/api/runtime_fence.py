"""Fence: a business write is accepted only while the attempt that makes it still owns the durable job."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..locks import user_critical_section
from ..models import OutboxMessage, Run, RunAttempt


@contextmanager
def fenced_run(db: Session, run: Run, attempt_id: str | None) -> Iterator[Run]:
    """The one way a service writes run-owned rows: hold the user lock, fence the attempt, then read
    the committed row inside that lock and yield it. Fencing first is what makes the read safe: the
    UPDATE takes the row lock the agent's claim, ACK and release paths also take, so the refresh that
    follows cannot race a status change the way a read before the lock could."""
    with user_critical_section(db, run.user_id):
        fence_attempt(db, run.id, attempt_id)
        db.refresh(run)
        from ..memory_recall.policy import source_available

        if not source_available(db, run):
            raise HTTPException(409, "run_source_unavailable")
        yield run


def fence_attempt(db, run_id: str, attempt_id: str | None) -> None:
    """Call inside the caller's user critical section, before the business write, in the same transaction.

    Each statement rewrites a column to its own value purely to take the write lock that the claim, ACK and
    release paths also take. SQLite has no SELECT ... FOR UPDATE, so a plain read would let an ownership change
    land between this check and the commit. PostgreSQL takes the same row lock. The lock lasts to commit.
    """
    if not attempt_id:
        raise HTTPException(409, "stale_attempt")
    owned = db.execute(
        update(OutboxMessage)
        .where(OutboxMessage.claim_token == attempt_id, OutboxMessage.claimed_until > datetime.now(UTC))
        .values(claim_token=OutboxMessage.claim_token)
        .execution_options(synchronize_session=False)
    ).rowcount
    active = db.execute(
        update(RunAttempt)
        .where(RunAttempt.id == attempt_id, RunAttempt.run_id == run_id, RunAttempt.ended_at.is_(None))
        .values(ended_at=RunAttempt.ended_at)
        .execution_options(synchronize_session=False)
    ).rowcount
    if owned != 1 or active != 1:
        raise HTTPException(409, "stale_attempt")
