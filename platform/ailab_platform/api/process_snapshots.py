"""API-owned transactional materialization and bounded repair of ProcessView V1."""

import copy
import json
import logging

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..contracts.process import ProcessView
from ..models import Run, RunEvent, RunProcessSnapshot, RunStatus, utcnow
from .process_view import PROCESS_EVENT_TYPES, PROCESS_SNAPSHOT_SCHEMA_VERSION, process_items, reconcile_terminal, reduce_event

log = logging.getLogger(__name__)
STOPPED = (*RunStatus.TERMINAL, RunStatus.INTERRUPTED)
# Refuse an oversized projection rather than silently destroy historical content.
# Refused runs retain their events. Limits also bound transient normalization work.
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 50000
MAX_ITEMS = 10000
_repair_cursor = ""


class ProcessTooLarge(ValueError):
    pass


def latest_seq():
    # Cover all events conservatively, including future process event types.
    return select(func.coalesce(func.max(RunEvent.seq), 0)).where(RunEvent.run_id == Run.id).correlate(Run).scalar_subquery()


def valid_snapshot(snapshot, through_seq=0):
    return snapshot is not None and snapshot.schema_version == PROCESS_SNAPSHOT_SCHEMA_VERSION and snapshot.through_seq >= through_seq


def derive(db, run_id):
    run = db.get(Run, run_id)
    items = []
    through_seq = 0
    snapshot = db.get(RunProcessSnapshot, run_id, populate_existing=True)
    first = db.scalar(select(RunEvent.seq).where(RunEvent.run_id == run_id).order_by(RunEvent.seq).limit(1))
    if valid_snapshot(snapshot) and (first is None or first > 1):
        # GC may already have removed a prefix of the trace. A later public
        # event must extend the durable projection, not erase that prefix.
        items = copy.deepcopy(snapshot.items)
        through_seq = snapshot.through_seq
    input_bytes = 0
    count = 0
    # Keyset pages bound ORM row retention. Ignore answer chunks: agent.message
    # contains the confirmed full text, including narration before a later action.
    # Read unconfirmed deltas only for stopped runs that lack that answer.
    while True:
        rows = db.execute(
            select(RunEvent.seq, RunEvent.type, RunEvent.payload)
            .where(RunEvent.run_id == run_id, RunEvent.seq > through_seq)
            .order_by(RunEvent.seq)
            .limit(200)
        ).all()
        if not rows:
            break
        for seq, type_, payload in rows:
            through_seq = seq
            count += 1
            if count > MAX_EVENTS:
                raise ProcessTooLarge("process event limit exceeded")
            if type_ not in PROCESS_EVENT_TYPES:
                # Non-display events still end a thinking block, like live SSE.
                if items and items[-1]["kind"] == "thinking":
                    items[-1]["done"] = True
                continue
            input_bytes += len(json.dumps(payload, ensure_ascii=False).encode())
            if input_bytes > MAX_INPUT_BYTES:
                raise ProcessTooLarge("process input limit exceeded")
            reduce_event(items, type_, payload)
            if len(items) > MAX_ITEMS:
                raise ProcessTooLarge("process item limit exceeded")
    items = ProcessView(
        source="events", items=process_items(reconcile_terminal(items, run.status if run else ""), summary=run.summary if run else "")
    ).items
    size = len(json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode())
    if size > MAX_SNAPSHOT_BYTES:
        raise ProcessTooLarge("process snapshot limit exceeded")
    return items, through_seq, size


def materialize(db, run_id):
    run = db.get(Run, run_id)
    if run is None or run.status not in STOPPED:
        return None
    # Same owner lock as event writes/resume; never replace a newer projection.
    from ..locks import user_critical_section

    with user_critical_section(db, run.user_id):
        db.refresh(run)
        if run.status not in STOPPED:
            return None
        items, seq, size = derive(db, run_id)
        snapshot = db.get(RunProcessSnapshot, run_id, populate_existing=True)
        if valid_snapshot(snapshot, seq):
            return snapshot
        if snapshot is None:
            snapshot = RunProcessSnapshot(run_id=run_id, created_at=utcnow())
            db.add(snapshot)
        snapshot.schema_version = PROCESS_SNAPSHOT_SCHEMA_VERSION
        snapshot.through_seq = seq
        snapshot.items = items
        snapshot.item_count = len(items)
        snapshot.uncompressed_bytes = size
        snapshot.updated_at = utcnow()
        db.flush()
        return snapshot


def try_materialize(db, run_id):
    try:
        with db.begin_nested():
            return materialize(db, run_id)
    except Exception:
        # The stop must commit even if projection fails; repair retries and GC
        # independently verifies coverage before deleting a single event.
        log.warning("process materialization deferred", exc_info=True)
        return None


@event.listens_for(Session, "before_commit")
def materialize_pending(db):
    if db.in_nested_transaction():
        return
    pending = db.info.pop("process_snapshot_runs", set())
    for run_id in sorted(pending):
        try_materialize(db, run_id)


@event.listens_for(Session, "after_rollback")
def clear_pending(db):
    if not db.in_nested_transaction():
        db.info.pop("process_snapshot_runs", None)


def repair_snapshots(db):
    global _repair_cursor
    # Page before inspecting coverage: healthy history must cost one bounded page.
    ids = list(db.scalars(select(Run.id).where(Run.id > _repair_cursor).order_by(Run.id).limit(min(settings.gc_batch_size, 10))))
    _repair_cursor = ids[-1] if ids else ""
    candidates = (
        list(
            db.execute(
                select(Run.id, latest_seq(), RunProcessSnapshot)
                .outerjoin(RunProcessSnapshot, RunProcessSnapshot.run_id == Run.id)
                .where(Run.id.in_(ids), Run.status.in_(STOPPED))
            )
        )
        if ids
        else []
    )
    repaired = 0
    for run_id, seq, snapshot in candidates:
        if valid_snapshot(snapshot, seq):
            continue
        repaired += try_materialize(db, run_id) is not None
        db.commit()
    return repaired
