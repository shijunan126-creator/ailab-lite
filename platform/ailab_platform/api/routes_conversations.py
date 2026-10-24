"""Conversations: chat threads. Every message starts a run (one turn); the turns share history and a workspace."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from ..deps import current_user, get_db
from ..library import owned_project
from ..locks import user_critical_section
from ..models import Conversation, Run, RunStatus, User, utcnow
from ..run_state import transition_run
from ..schemas import ConversationCreate, ConversationDetail, ConversationFork, ConversationOut, ConversationPatch, MessageCreate, RunDetail
from .routes_runs import start_run
from .serializers import run_dict

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


def _owned(db: Session, conversation_id: str, user: User) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id or conv.deleted_at is not None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


def _turns(db: Session, conv: Conversation) -> list[Run]:
    return list(
        db.scalars(select(Run).where(Run.conversation_id == conv.id, Run.superseded_by_run_id.is_(None)).order_by(Run.created_at, Run.id))
    )


def conversation_out(conv: Conversation, last_status: str | None = None) -> dict:
    return {
        "id": conv.id,
        "forked_from": conv.forked_from,
        "project_id": conv.project_id,
        "title": conv.title,
        "model": conv.model,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "last_status": last_status,
    }


def latest_previews(turns: list[Run]) -> list[dict]:
    """The newest preview of every file the conversation produced (a later turn's render replaces an earlier one)."""
    by_file: dict[str, dict] = {}
    for run in turns:
        for preview in (run.result or {}).get("previews") or []:
            if isinstance(preview, dict) and preview.get("filename"):
                by_file.pop(preview["filename"], None)  # re-insert: most recently touched last
                by_file[preview["filename"]] = {**preview, "run_id": run.id}  # preview URLs are per run
    return list(by_file.values())


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    limit: int = Query(default=100, ge=1, le=300),
    project_id: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    if project_id is not None:
        owned_project(db, project_id, user)
    last_status = (
        select(Run.status)
        .where(Run.conversation_id == Conversation.id, Run.superseded_by_run_id.is_(None))
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    rows = db.execute(
        select(Conversation, last_status)
        .where(Conversation.user_id == user.id, Conversation.deleted_at.is_(None), Conversation.project_id == project_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    ).all()
    return [conversation_out(conv, last) for conv, last in rows]


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(body: ConversationCreate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    with user_critical_section(db, user.id):
        if body.project_id is not None:
            owned_project(db, body.project_id, user)
        conv = Conversation(user_id=user.id, title=(body.title or "").strip()[:200], model=body.model or "", project_id=body.project_id)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        return conversation_out(conv)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str,
    before: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    conv = _owned(db, conversation_id, user)
    query = select(Run).where(Run.conversation_id == conv.id, Run.superseded_by_run_id.is_(None), Run.user_id == user.id)
    if before:
        cursor = db.get(Run, before)
        if cursor is None or cursor.conversation_id != conv.id:
            raise HTTPException(status_code=404, detail="turn not found")
        query = query.where(tuple_(Run.created_at, Run.id) < (cursor.created_at, cursor.id))
    rows = list(db.scalars(query.order_by(Run.created_at.desc(), Run.id.desc()).limit(limit + 1)))
    turns = list(reversed(rows[:limit]))
    last_status = db.scalar(
        select(Run.status)
        .where(Run.conversation_id == conv.id, Run.superseded_by_run_id.is_(None))
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(1)
    )
    display_turns = []
    for run in turns:
        out = run_dict(db, run, detail=True)
        out["result"] = {
            key: value for key, value in out["result"].items() if key in {"previews", "thinking", "plan", "question", "resumable"}
        }
        display_turns.append(out)
    return {
        **conversation_out(conv, last_status),
        "turns": display_turns,
        "previews": latest_previews(turns),
        "has_more": len(rows) > limit,
    }


@router.patch("/{conversation_id}", response_model=ConversationOut)
def rename_conversation(
    conversation_id: str, body: ConversationPatch, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    with user_critical_section(db, user.id):
        conv = _owned(db, conversation_id, user)
        if "project_id" in body.model_fields_set:
            if body.project_id is not None:
                owned_project(db, body.project_id, user)
            conv.project_id = body.project_id
        if "title" in body.model_fields_set:
            conv.title = body.title.strip()[:200]
        conv.updated_at = utcnow()
        db.commit()
        return conversation_out(conv)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    """Hidden from the list (the runs and the files they produced stay in the library). A running turn is canceled."""
    with user_critical_section(db, user.id):
        conv = _owned(db, conversation_id, user)
        pending = db.scalars(
            select(Run).where(Run.conversation_id == conv.id, Run.superseded_by_run_id.is_(None), Run.status.not_in(RunStatus.TERMINAL))
        )
        for run in pending:
            transition_run(db, run.id, RunStatus.CANCELED, reason="conversation_deleted", commit=False)
        conv.deleted_at = utcnow()
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{conversation_id}/messages", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
def send_message(conversation_id: str, body: MessageCreate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    conv = _owned(db, conversation_id, user)
    run = start_run(db, user, body.prompt, body.model, body.file_ids, conversation=conv, mode=body.mode, skill=body.skill)
    return run_dict(db, run, detail=True)


@router.post("/{conversation_id}/fork", response_model=ConversationOut, status_code=201)
def fork_conversation(conversation_id: str, body: ConversationFork, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Copy at most 500 turns, 100k events and 2 GiB of derived objects; never copy ledger or file bytes."""
    import time
    from copy import deepcopy
    from datetime import UTC, datetime, timedelta

    from ..models import Artifact, RunEvent
    from ..storage import get_storage
    from .routes_internal import workspace_alive, workspace_data, workspace_key

    storage, copied_keys = get_storage(), []
    total_bytes, total_events = 0, 0
    deadline = time.monotonic() + 60

    def check_deadline():
        if time.monotonic() >= deadline:
            raise HTTPException(422, "分叉复制超过60秒，请缩小范围后重试")

    try:
        with user_critical_section(db, user.id):
            source = _owned(db, conversation_id, user)
            db.refresh(source)
            target = db.get(Run, body.run_id, populate_existing=True)
            if not target or target.conversation_id != source.id or target.superseded_by_run_id:
                raise HTTPException(404, "轮次不存在")
            if target.status not in RunStatus.TERMINAL:
                raise HTTPException(422, "只能从已结束的轮次分叉")
            turns = list(
                db.scalars(
                    select(Run)
                    .where(
                        Run.conversation_id == source.id,
                        Run.superseded_by_run_id.is_(None),
                        tuple_(Run.created_at, Run.id) <= (target.created_at, target.id),
                    )
                    .order_by(Run.created_at, Run.id)
                    .limit(501)
                )
            )
            if len(turns) > 500:
                raise HTTPException(422, "分叉最多复制500轮对话")
            if any((r.options or {}).get("mode", "work") != "chat" for r in turns) and body.mode != "work":
                raise HTTPException(422, "包含工作轮次的对话只能分叉为工作模式")
            conv = Conversation(
                user_id=user.id,
                project_id=source.project_id,
                title=source.title,
                model=source.model,
                forked_from={"conversation_id": source.id, "run_id": target.id},
            )
            db.add(conv)
            db.flush()
            mapping = {}

            def copy_object(src, dst):
                nonlocal total_bytes
                check_deadline()
                total_bytes += storage.info(src).size
                if total_bytes > 2 * 1024**3 or len(copied_keys) >= 10000:
                    raise HTTPException(422, "分叉对象超过2GiB或10000个上限")
                copied_keys.append(dst)
                storage.copy(src, dst)

            previous_created = None
            for old in turns:
                check_deadline()
                if old.status not in RunStatus.TERMINAL:
                    raise HTTPException(422, "复制范围中存在未结束的轮次")
                result = deepcopy(old.result or {})
                result["forked_from_run_id"] = old.id
                result["forked_usage"] = run_dict(db, old, detail=True)["usage"]
                for key in ("workspace_saved", "workspace_key", "workspace_saved_at", "workspace_bytes"):
                    result.pop(key, None)
                created = old.created_at
                if previous_created is not None and created <= previous_created:
                    created = previous_created + timedelta(microseconds=1)
                previous_created = created
                new = Run(
                    user_id=user.id,
                    conversation_id=conv.id,
                    title=old.title,
                    prompt=old.prompt,
                    summary=old.summary,
                    error=old.error,
                    status=old.status,
                    options=deepcopy(old.options),
                    model=old.model,
                    input_file_ids=deepcopy(old.input_file_ids),
                    created_at=created,
                    started_at=old.started_at,
                    finished_at=old.finished_at,
                    work_ms=old.work_ms,
                    result=result,
                )
                db.add(new)
                db.flush()
                mapping[old.id] = new
                for artifact in db.scalars(select(Artifact).where(Artifact.run_id == old.id)):
                    db.add(Artifact(run_id=new.id, file_id=artifact.file_id, role=artifact.role))
                # Copy events in pages to bound memory; the source can subsequently be purged independently.
                after = 0
                while True:
                    check_deadline()
                    events = list(
                        db.scalars(
                            select(RunEvent).where(RunEvent.run_id == old.id, RunEvent.seq > after).order_by(RunEvent.seq).limit(500)
                        )
                    )
                    if not events:
                        break
                    total_events += len(events)
                    if total_events > 100000:
                        raise HTTPException(422, "分叉过程记录超过100000条上限")
                    for event in events:
                        db.add(
                            RunEvent(
                                run_id=new.id, seq=event.seq, type=event.type, payload=deepcopy(event.payload), created_at=event.created_at
                            )
                        )
                    after = events[-1].seq
                    db.flush()
                cursor = None
                while True:
                    page = storage.list_prefix(f"previews/{old.id}/", cursor=cursor, limit=100)
                    for obj in page:
                        copy_object(obj.key, obj.key.replace(f"previews/{old.id}/", f"previews/{new.id}/", 1))
                    cursor = page.cursor
                    if cursor is None:
                        break
            if source.summary_through_run_id in mapping:
                conv.summary = source.summary
                conv.summary_through_run_id = mapping[source.summary_through_run_id].id
            if body.mode == "work":
                newest = next((r for r in reversed(turns) if workspace_data(r) or "workspace_saved" in (r.result or {})), None)
                if newest:
                    if not workspace_data(newest):
                        raise HTTPException(422, "分叉点工作区已过期或不可用")
                    key = workspace_data(newest).get("key") or workspace_key(newest.id)
                    if not workspace_alive(newest, datetime.now(UTC)) or not storage.exists(key):
                        raise HTTPException(422, "分叉点工作区已过期或不可用")
                    copied = mapping[newest.id]
                    dest = workspace_key(copied.id)
                    copy_object(key, dest)
                    copied.result = {
                        **copied.result,
                        "workspace_saved": True,
                        "workspace_key": dest,
                        "workspace_saved_at": datetime.now(UTC).isoformat(),
                        "workspace_bytes": storage.info(dest).size,
                    }
            db.commit()
            return conversation_out(conv, target.status)
    except BaseException:
        db.rollback()
        for key in copied_keys:
            try:
                storage.delete(key)
            except Exception:
                pass  # orphan preview/workspace objects are also covered by the reaper
        raise
