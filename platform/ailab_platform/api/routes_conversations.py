"""Conversations: chat threads. Every message starts a run (one turn); the turns share history and a workspace."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..conversation_tree import path_ids, path_runs, require_idle
from ..deps import current_user, get_db
from ..library import move_lineage, owned_project
from ..locks import user_critical_section
from ..model_policy import ModelPolicy
from ..models import Artifact, Conversation, FileObject, Run, RunStatus, User, utcnow
from ..run_state import transition_run
from ..schemas import (
    ConversationActive,
    ConversationCreate,
    ConversationDetail,
    ConversationFork,
    ConversationOut,
    ConversationPatch,
    MessageCreate,
    RunDetail,
)
from .routes_runs import start_run
from .serializers import run_dict, run_dicts

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


def _owned(db: Session, conversation_id: str, user: User) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id or conv.deleted_at is not None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


def conversation_out(conv: Conversation, last_status: str | None = None) -> dict:
    return {
        "memory_policy": conv.memory_policy or {},
        "id": conv.id,
        "pinned_at": conv.pinned_at,
        "active_run_id": conv.active_run_id,
        "forked_from": conv.forked_from,
        "project_id": conv.project_id,
        "title": conv.title,
        "model": conv.model,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "last_status": last_status,
    }


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    limit: int = Query(default=100, ge=1, le=300),
    project_id: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    if project_id is not None:
        owned_project(db, project_id, user)
    last_status = select(Run.status).where(Run.id == Conversation.active_run_id).scalar_subquery()
    rows = db.execute(
        select(Conversation, last_status)
        .where(Conversation.user_id == user.id, Conversation.deleted_at.is_(None), Conversation.project_id == project_id)
        .order_by(Conversation.pinned_at.desc().nulls_last(), Conversation.updated_at.desc(), Conversation.id.desc())
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
    ids = path_ids(db, conv.id, conv.active_run_id)
    stop = len(ids)
    if before:
        if before not in ids:
            raise HTTPException(404, "轮次不在当前路径")
        stop = ids.index(before)
    page_ids = ids[max(0, stop - limit) : stop]
    rows = {r.id: r for r in db.scalars(select(Run).where(Run.conversation_id == conv.id, Run.id.in_(page_ids)))}
    turns = [rows[id_] for id_ in page_ids]
    leaf = db.get(Run, conv.active_run_id) if conv.active_run_id else None
    last_status = leaf.status if leaf else None
    parents = {run.parent_run_id for run in turns}
    siblings_by_parent = {}
    if parents:
        window = (
            select(
                Run.id,
                Run.parent_run_id,
                func.row_number().over(partition_by=Run.parent_run_id, order_by=(Run.created_at, Run.id)).label("position"),
            )
            .where(
                Run.conversation_id == conv.id,
                or_(Run.parent_run_id.in_(parents - {None}), Run.parent_run_id.is_(None) if None in parents else False),
            )
            .subquery()
        )
        for rid, parent, _ in db.execute(select(window).where(window.c.position <= 50).order_by(window.c.position)):
            siblings_by_parent.setdefault(parent, []).append(rid)
    display_turns = run_dicts(db, turns)
    for run, out in zip(turns, display_turns, strict=True):
        siblings = siblings_by_parent[run.parent_run_id]
        out["versions"] = {"index": siblings.index(run.id) + 1, "count": len(siblings), "ids": siblings}
        out["result"] = {
            key: value
            for key, value in out["result"].items()
            if key in {"previews", "thinking", "plan", "question", "resumable", "sources", "forked_from_run_id", "genui"}
        }
    from .inbox import pending_query, queue_output

    return {
        "queue": [queue_output(row) for row in db.scalars(pending_query(conv.id).limit(5))],
        "queue_paused": conv.queue_paused,
        **conversation_out(conv, last_status),
        "turns": display_turns,
        "previews": path_previews(db, conv.id, ids),
        "context": conversation_context(db, leaf),
        "has_more": stop > limit,
    }


@router.patch("/{conversation_id}", response_model=ConversationOut)
def rename_conversation(
    conversation_id: str, body: ConversationPatch, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    with user_critical_section(db, user.id):
        conv = _owned(db, conversation_id, user)
        moved = None
        if body.move_files:
            # Artifact.run_id and Run.conversation_id are indexed; lineage lookup uses its unique head index.
            lineages = (
                select(FileObject.lineage_id)
                .join(Artifact, Artifact.file_id == FileObject.id)
                .join(Run, Run.id == Artifact.run_id)
                .where(
                    Run.conversation_id == conv.id,
                    Run.user_id == user.id,
                    FileObject.user_id == user.id,
                    FileObject.purpose == "library",
                    FileObject.deleted_at.is_(None),
                    FileObject.purged_at.is_(None),
                )
            )
            heads = select(FileObject).where(
                FileObject.lineage_id.in_(lineages),
                FileObject.is_head == 1,
                FileObject.deleted_at.is_(None),
                FileObject.purged_at.is_(None),
            )
            # Validate before any mutation; keyset batches bound memory, while every move commits together.
            if body.project_id is not None:
                owned_project(db, body.project_id, user)
            moved, after = 0, ""
            while batch := db.scalars(heads.where(FileObject.id > after).order_by(FileObject.id).limit(200)).all():
                for head in batch:
                    move_lineage(db, head, body.project_id)
                moved += len(batch)
                after = batch[-1].id
        if "project_id" in body.model_fields_set:
            if body.project_id is not None:
                owned_project(db, body.project_id, user)
            conv.project_id = body.project_id
        if "pinned" in body.model_fields_set:
            conv.pinned_at = utcnow() if body.pinned else None
        if body.memory_policy is not None:
            conv.memory_policy = body.memory_policy.model_dump()
        if "title" in body.model_fields_set:
            conv.title = body.title.strip()[:200]
        conv.updated_at = utcnow()
        db.commit()
        return {**conversation_out(conv), "moved_files": moved}


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    """Hidden from the list (the runs and the files they produced stay in the library). A running turn is canceled."""
    with user_critical_section(db, user.id):
        conv = _owned(db, conversation_id, user)
        pending = db.scalars(select(Run).where(Run.conversation_id == conv.id, Run.status.not_in(RunStatus.TERMINAL)))
        for run in pending:
            transition_run(db, run.id, RunStatus.CANCELED, reason="conversation_deleted", commit=False)
        from ..models import QueuedMessage

        db.execute(
            update(QueuedMessage)
            .where(QueuedMessage.conversation_id == conv.id, QueuedMessage.state == "queued")
            .values(state="canceled", updated_at=utcnow())
        )
        conv.deleted_at = utcnow()
        # what the memory system kept of this conversation (episode copies, source quotes) goes with it
        from ..memory_recall.lineage import forget_conversation

        forget_conversation(db, user, conv.id)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{conversation_id}/messages", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
def send_message(conversation_id: str, body: MessageCreate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    conv = _owned(db, conversation_id, user)
    run = start_run(
        db,
        user,
        body.prompt,
        body.model,
        body.file_ids,
        conversation=conv,
        mode=body.mode if "mode" in body.model_fields_set else None,
        skill=body.skill,
        template=body.template,
        client_send_id=body.client_send_id,
        client="portal",
    )
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
            if not target or target.conversation_id != source.id:
                raise HTTPException(404, "轮次不存在")
            if target.status not in RunStatus.TERMINAL:
                raise HTTPException(422, "只能从已结束的轮次分叉")
            turns = path_runs(db, source.id, target.id)
            if len(turns) > 500:
                raise HTTPException(422, "分叉最多复制500轮对话")
            # A work branch takes the whole history into work, so nothing in it can branch back into chat.
            worked = (source.forked_from or {}).get("mode") == "work" or any((r.options or {}).get("mode", "work") != "chat" for r in turns)
            if worked and body.mode != "work":
                raise HTTPException(422, "包含工作轮次的对话只能分叉为工作模式")
            title = source.title or (turns[0].title if turns else "")
            conv = Conversation(
                user_id=user.id,
                project_id=source.project_id,
                memory_policy=dict(source.memory_policy or {}),
                title=f"{title} · 分支"[:200] if title else "",
                model=source.model,
                forked_from={"conversation_id": source.id, "run_id": target.id, "mode": body.mode},
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

            from .fork_context import copy_message_sources, copy_summary
            from .source_lifecycle import invalid_history

            invalid_sources = invalid_history(db, [turn.id for turn in turns])
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
                    context_source_epoch=old.context_source_epoch,
                    conversation_id=conv.id,
                    parent_run_id=mapping[old.parent_run_id].id if old.parent_run_id in mapping else None,
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
                if old.id in invalid_sources:
                    new.runtime_meta = {**(new.runtime_meta or {}), "source_invalidated": True}
                from ..models import QueuedMessage
                from .inbox import steers_for_run

                applied = (old.runtime_meta or {}).get("steer_ids", [])
                copied_steers = {}
                if applied:
                    for row in steers_for_run(db, old):
                        if row.id not in applied:
                            continue
                        copied = QueuedMessage(
                            client_send_id=None,  # A fork copies history, never the original submission identity.
                            conversation_id=conv.id,
                            user_id=user.id,
                            prompt=row.prompt,
                            file_ids=deepcopy(row.file_ids),
                            model=row.model,
                            options=deepcopy(row.options),
                            steer=True,
                            state="steered",
                            run_id=new.id,
                            after_run_id=new.id,
                            created_at=row.created_at,
                            updated_at=row.updated_at,
                        )
                        db.add(copied)
                        db.flush()
                        copied_steers[row.id] = copied.id
                    new.runtime_meta = {
                        **(new.runtime_meta or {}),
                        "steer_ids": [copied_steers[qid] for qid in applied if qid in copied_steers],
                    }
                copy_message_sources(db, old, new, copied_steers)
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
                        if event.type in {"client.call", "client.result"}:
                            continue
                        payload = deepcopy(event.payload)
                        if event.type == "agent.steered" and payload.get("queued_id") in copied_steers:
                            payload["queued_id"] = copied_steers[payload["queued_id"]]
                        db.add(RunEvent(run_id=new.id, seq=event.seq, type=event.type, payload=payload, created_at=event.created_at))
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
            conv.active_run_id = mapping[target.id].id
            copy_summary(db, source, conv, mapping, invalid_sources)
            if body.mode == "work":
                newest = next((r for r in reversed(turns) if workspace_data(r) or "workspace_saved" in (r.result or {})), None)
                if newest:
                    if newest.id in invalid_sources:
                        raise HTTPException(422, "分叉点工作区包含已失效来源")
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


def conversation_context(db, leaf):
    from ..catalog import get_catalog
    from ..context_settings import context_settings
    from ..models import ModelUsage

    usage = (
        db.scalar(
            select(ModelUsage)
            .where(ModelUsage.run_id == leaf.id, ModelUsage.quiet.is_(False), ModelUsage.status == "settled")
            .order_by(ModelUsage.created_at.desc(), ModelUsage.id.desc())
            .limit(1)
        )
        if leaf
        else None
    )
    model = (usage.alias or usage.model) if usage else (leaf.model if leaf else None)
    config = context_settings(db)
    spec = get_catalog().get(model) if model else None
    return {
        "used_tokens": usage.prompt_tokens if usage else None,
        "model": model,
        "compact_at_tokens": ModelPolicy.compaction_threshold(spec, config) if spec and config["auto_compact"] else None,
    }


@router.post("/{conversation_id}/active", response_model=ConversationDetail)
def select_active(conversation_id: str, body: ConversationActive, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        conv = _owned(db, conversation_id, user)
        db.refresh(conv)
        require_idle(db, conv.id)
        run = db.get(Run, body.run_id)
        if not run or run.conversation_id != conv.id:
            raise HTTPException(404, "轮次不存在")
        expected = conv.active_run_id
        depth = len(path_ids(db, conv.id, run.id))
        while True:
            child = db.scalar(
                select(Run.id)
                .where(Run.conversation_id == conv.id, Run.parent_run_id == run.id)
                .order_by(Run.created_at.desc(), Run.id.desc())
                .limit(1)
            )
            if child is None:
                break
            depth += 1
            if depth > 500:
                raise HTTPException(422, "对话路径最多500轮")
            run = db.get(Run, child)
        changed = db.execute(
            update(Conversation)
            .where(Conversation.id == conv.id, Conversation.active_run_id == expected)
            .values(active_run_id=run.id, updated_at=utcnow())
        ).rowcount
        if not changed:
            raise HTTPException(409, "对话路径已改变，请刷新后重试")
        if expected != run.id:
            conv.queue_paused = True
        db.commit()
    return get_conversation(conversation_id, before=None, limit=20, user=user, db=db)


def path_previews(db, conversation_id, ids):
    rows = dict(db.execute(select(Run.id, Run.result["previews"]).where(Run.conversation_id == conversation_id, Run.id.in_(ids))).all())
    by_file = {}
    for id_ in ids:
        for preview in rows.get(id_) or []:
            if isinstance(preview, dict) and preview.get("filename"):
                by_file.pop(preview["filename"], None)
                by_file[preview["filename"]] = {**preview, "run_id": id_}
    return list(by_file.values())
