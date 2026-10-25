"""User-owned bounded inbox and queued messages."""

import base64
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..contracts.inbox import (
    NotificationRead,
    NotificationsOut,
    QueueCreate,
    QueuedMessageOut,
    QueuePatch,
    SteerClaimOut,
    UnreadOut,
)
from ..deps import current_user, get_db
from ..locks import user_critical_section
from ..models import Notification, QueuedMessage, Run, RunStatus, User, utcnow
from ..service_auth import require_scope
from .inbox import dispatch_next, pending_query, queue_output, steers_for_run
from .routes_conversations import _owned
from .routes_runs import validate_run_input
from .runtime_fence import fence_attempt

router = APIRouter(tags=["inbox"])


def unread_count(db, user_id):
    return db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None)))


@router.get("/v1/notifications/unread", response_model=UnreadOut)
def unread(user: User = Depends(current_user), db: Session = Depends(get_db)):
    latest = db.scalar(
        select(Notification.created_at)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(1)
    )
    return {"unread": unread_count(db, user.id), "latest_at": latest}


@router.get("/v1/notifications", response_model=NotificationsOut)
def notifications(
    cursor: str | None = Query(default=None, max_length=300),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(Notification).where(Notification.user_id == user.id)
    if cursor:
        try:
            at, id_ = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            at = datetime.fromisoformat(at)
            if not isinstance(id_, str) or len(id_) > 40:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, "invalid cursor") from None
        query = query.where(or_(Notification.created_at < at, (Notification.created_at == at) & (Notification.id < id_)))
    rows = db.scalars(query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit + 1)).all()
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = base64.urlsafe_b64encode(json.dumps([last.created_at.isoformat(), last.id]).encode()).decode()
    return {
        "items": [{c.name: getattr(row, c.name) for c in Notification.__table__.columns if c.name != "user_id"} for row in rows[:limit]],
        "next_cursor": next_cursor,
        "unread": unread_count(db, user.id),
    }


@router.post("/v1/notifications/read")
def mark_read(body: NotificationRead, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        query = update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
        if body.ids is not None:
            query = query.where(Notification.id.in_(body.ids))
        elif body.conversation_id is not None:
            query = query.where(Notification.conversation_id == body.conversation_id)
        db.execute(query.values(read_at=utcnow()))
        count = unread_count(db, user.id)
        db.commit()
        return {"unread": count}


@router.post("/v1/conversations/{conversation_id}/queue", response_model=QueuedMessageOut, status_code=201)
def enqueue(conversation_id: str, body: QueueCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        conv = _owned(db, conversation_id, user)
        db.refresh(conv)
        leaf = db.get(Run, conv.active_run_id, populate_existing=True) if conv.active_run_id else None
        if not leaf or leaf.status not in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_USER}:
            raise HTTPException(409, {"code": "conversation_idle", "message": "对话空闲，请直接发送"})
        if len(db.scalars(pending_query(conv.id).limit(5)).all()) >= 5:
            raise HTTPException(422, "最多排队5条消息")
        if body.template is not None and body.steer:
            raise HTTPException(400, "模板只能用于新一轮，不能插话")
        model, options, files = validate_run_input(db, user, body.prompt, body.model, body.file_ids, body.mode, body.skill, body.template)
        row = QueuedMessage(
            conversation_id=conv.id,
            user_id=user.id,
            prompt=body.prompt,
            model=model,
            options=options,
            file_ids=files,
            steer=body.steer,
            after_run_id=leaf.id,
        )
        db.add(row)
        db.commit()
        return queue_output(row)


def owned_queue(db, conversation_id, qid, user):
    _owned(db, conversation_id, user)
    row = db.get(QueuedMessage, qid, populate_existing=True)
    if not row or row.conversation_id != conversation_id or row.user_id != user.id:
        raise HTTPException(404, "排队消息不存在")
    if row.state != "queued":
        raise HTTPException(409, "消息已被处理")
    return row


@router.patch("/v1/conversations/{conversation_id}/queue/{qid}", response_model=QueuedMessageOut)
def patch_queue(conversation_id: str, qid: str, body: QueuePatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        row = owned_queue(db, conversation_id, qid, user)
        if body.prompt is not None:
            validate_run_input(db, user, body.prompt, row.model, row.file_ids, row.options.get("mode", "work"), row.options.get("skill"))
            row.prompt = body.prompt
        if body.steer and row.options.get("template"):
            raise HTTPException(400, "模板只能用于新一轮，不能插话")
        if body.steer is not None:
            row.steer = body.steer
        row.updated_at = utcnow()
        db.commit()
        return queue_output(row)


@router.delete("/v1/conversations/{conversation_id}/queue/{qid}", status_code=204)
def delete_queue(conversation_id: str, qid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        row = owned_queue(db, conversation_id, qid, user)
        row.state, row.updated_at = "canceled", utcnow()
        db.commit()
    return Response(status_code=204)


@router.post("/v1/conversations/{conversation_id}/queue/resume", response_model=QueuedMessageOut)
def resume_queue(conversation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..conversation_tree import require_idle

    with user_critical_section(db, user.id):
        conv = _owned(db, conversation_id, user)
        db.refresh(conv)
        require_idle(db, conv.id)
        conv.queue_paused = False
        row = dispatch_next(db, conv)
        db.commit()
        if row is None:
            raise HTTPException(409, "队列为空或消息暂时无法启动")
        return queue_output(row)


@router.post("/internal/runs/{run_id}/steer/claim", response_model=SteerClaimOut, dependencies=[Depends(require_scope("runs:steer"))])
def claim_steers(run_id: str, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    with user_critical_section(db, run.user_id):
        fence_attempt(db, run_id, x_run_attempt)
        db.refresh(run)
        if run.status != RunStatus.RUNNING:
            raise HTTPException(409, "run_not_running")
        if run.conversation_id:
            conv = _owned(db, run.conversation_id, db.get(User, run.user_id))
            db.refresh(conv)
            if conv.active_run_id == run.id and not conv.queue_paused:
                db.execute(
                    update(QueuedMessage)
                    .where(QueuedMessage.conversation_id == conv.id, QueuedMessage.state == "queued", QueuedMessage.steer.is_(True))
                    .values(state="steered", run_id=run.id, updated_at=utcnow())
                )
        applied = (run.runtime_meta or {}).get("steer_ids", [])
        rows = [queue_output(row) for row in steers_for_run(db, run, exclude=applied)]
        db.commit()
        return {"items": rows}
