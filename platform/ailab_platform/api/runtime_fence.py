"""Fence: a business write is accepted only while the attempt that makes it still owns the durable job."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import update

from ..models import OutboxMessage, RunAttempt


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
