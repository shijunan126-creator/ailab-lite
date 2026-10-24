"""Transactional notifications, queue dispatch and durable steer acknowledgement.

Callers hold the user's admission lock and commit the entire business transaction.
"""

from datetime import UTC

from fastapi import HTTPException
from sqlalchemy import delete, select

from ..config import settings
from ..events import append_event
from ..locks import user_critical_section
from ..models import Conversation, Notification, QueuedMessage, RunStatus, ScheduledTask, User, utcnow


def create_notification(db, user_id, kind, title, conversation_id=None, run_id=None, share_id=None):
    # PG's user row lock lasts to the caller's commit; SQLite's insert takes its
    # transaction write lock. Concurrent share and run producers prune serially.
    with user_critical_section(db, user_id):
        return _create_notification(db, user_id, kind, title, conversation_id, run_id, share_id)


def _create_notification(db, user_id, kind, title, conversation_id=None, run_id=None, share_id=None):
    row = Notification(user_id=user_id, kind=kind, title=title[:200], conversation_id=conversation_id, run_id=run_id, share_id=share_id)
    db.add(row)
    db.flush()
    newest = (
        select(Notification.id)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(200)
    )
    db.execute(delete(Notification).where(Notification.user_id == user_id, Notification.id.not_in(newest)))
    return row


def queue_output(row):
    return {
        "id": row.id,
        "prompt": row.prompt,
        "file_ids": row.file_ids,
        "model": row.model,
        "mode": row.options.get("mode", "work"),
        "skill": row.options.get("skill"),
        "steer": row.steer,
        "state": row.state,
        "created_at": row.created_at,
    }


def pending_query(conversation_id):
    return (
        select(QueuedMessage)
        .where(QueuedMessage.conversation_id == conversation_id, QueuedMessage.state == "queued")
        .order_by(QueuedMessage.created_at, QueuedMessage.id)
    )


def dispatch_next(db, conv):
    from .routes_runs import start_run

    row = db.scalar(pending_query(conv.id).limit(1))
    if row is None or conv.deleted_at is not None:
        return None
    try:
        # Business admission failure pauses the queue, never rolls back a valid completion.
        with db.begin_nested():
            run = start_run(
                db,
                db.get(User, conv.user_id),
                row.prompt,
                row.model,
                row.file_ids,
                conversation=conv,
                mode=row.options.get("mode", "work"),
                skill=row.options.get("skill"),
                locked=True,
                commit=False,
            )
            row.state, row.run_id, row.updated_at = "started", run.id, utcnow()
            db.flush()
        return row
    except HTTPException:
        conv.queue_paused = True
        return None


def on_transition(db, run):
    status = run.status
    if status not in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED, RunStatus.WAITING_USER}:
        return
    conv = db.get(Conversation, run.conversation_id, populate_existing=True) if run.conversation_id else None
    title = (conv.title if conv else "") or run.title
    kind = None
    if status == RunStatus.WAITING_USER:
        if not (run.runtime_meta or {}).get("notified_waiting"):
            kind = "run.waiting"
            run.runtime_meta = {**(run.runtime_meta or {}), "notified_waiting": True}
    elif status != RunStatus.CANCELED:
        schedule_id = (run.options or {}).get("schedule_id")
        if schedule_id:
            task = db.get(ScheduledTask, schedule_id)
            title = task.title if task else title
            kind = f"schedule.{status}"
        else:
            created = run.created_at.replace(tzinfo=UTC) if run.created_at.tzinfo is None else run.created_at
            finished = run.finished_at.replace(tzinfo=UTC) if run.finished_at.tzinfo is None else run.finished_at
            if (finished - created).total_seconds() >= settings.notify_after_seconds:
                kind = f"run.{status}"
    if kind:
        create_notification(db, run.user_id, kind, title, run.conversation_id, run.id)
    if not conv:
        return
    if conv.active_run_id != run.id or status in {RunStatus.FAILED, RunStatus.CANCELED}:
        conv.queue_paused = True
    elif status == RunStatus.SUCCEEDED and not conv.queue_paused and conv.deleted_at is None:
        dispatch_next(db, conv)


def steers_for_run(db, run, exclude=()):
    return db.scalars(
        select(QueuedMessage)
        .where(QueuedMessage.run_id == run.id, QueuedMessage.state == "steered", QueuedMessage.id.not_in(exclude))
        .order_by(QueuedMessage.created_at, QueuedMessage.id)
    ).all()


def acknowledge_steers(db, run, state):
    previous = (run.runtime_meta or {}).get("steer_ids", [])
    supplied = state.get("steer_ids", [])
    if (
        not isinstance(supplied, list)
        or any(not isinstance(qid, str) for qid in supplied)
        or len(set(supplied)) != len(supplied)
        or not set(previous).issubset(supplied)
    ):
        raise HTTPException(422, "invalid steer checkpoint")
    if supplied == previous:
        return supplied
    claimed = {row.id: row for row in steers_for_run(db, run, exclude=previous)}
    if any(qid not in claimed for qid in supplied if qid not in previous):
        raise HTTPException(422, "invalid steer checkpoint")
    for qid in supplied:
        if qid not in previous:
            row = claimed[qid]
            if {"role": "user", "content": row.prompt} not in state.get("messages", []):
                raise HTTPException(422, "steer message missing")
            append_event(db, run.id, "agent.steered", {"queued_id": qid, "prompt": row.prompt}, commit=False)
    return supplied
