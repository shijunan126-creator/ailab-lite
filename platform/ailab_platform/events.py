"""Run event helpers shared by the API and tests."""

from __future__ import annotations

import time

from sqlalchemy import case, func, insert, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .models import RunEvent, RunProcessSnapshot, utcnow


def append_event(db: Session, run_id: str, type_: str, payload: dict | None = None, *, commit: bool = True) -> RunEvent:
    """Append the next sequenced event for a run in one INSERT.

    The sequence is computed by the INSERT's own subquery, so this is one round
    trip instead of SELECT max(seq) + INSERT + COMMIT + REFRESH. SQLite has a
    single writer but internal API calls can still race; the unique constraint
    plus retry keeps the sequence dense.
    """
    last_error: Exception | None = None
    for attempt in range(6):
        created_at = utcnow()
        data = payload or {}
        raw_seq = select(func.coalesce(func.max(RunEvent.seq), 0)).where(RunEvent.run_id == run_id).scalar_subquery()
        # Snapshot coverage survives full GC and is the durable sequence floor.
        covered_seq = func.coalesce(select(RunProcessSnapshot.through_seq).where(RunProcessSnapshot.run_id == run_id).scalar_subquery(), 0)
        next_seq = case((raw_seq > covered_seq, raw_seq), else_=covered_seq) + 1
        stmt = (
            insert(RunEvent)
            .values(run_id=run_id, seq=next_seq, type=type_, payload=data, created_at=created_at)
            .returning(RunEvent.id, RunEvent.seq)
        )
        try:
            row = db.execute(stmt).one()
            if commit:
                db.commit()
            return RunEvent(
                id=row.id,
                run_id=run_id,
                seq=row.seq,
                type=type_,
                payload=data,
                created_at=created_at,
            )
        except (IntegrityError, OperationalError) as exc:  # pragma: no cover - race path
            if not commit:
                raise  # the caller owns this transaction and its rollback
            last_error = exc
            db.rollback()
            time.sleep(0.05 * (attempt + 1))
    raise RuntimeError(f"could not append event for {run_id}: {last_error}")


def list_events(db: Session, run_id: str, after: int = 0, limit: int | None = None) -> list[RunEvent]:
    query = select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after).order_by(RunEvent.seq)
    if limit is not None:
        query = query.limit(limit)
    return list(db.scalars(query))
