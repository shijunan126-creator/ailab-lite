"""Bounded model history pages and API-owned summary memory."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..contracts.model_context import SummaryUpdate
from ..conversation_tree import ancestor_query, path_ids
from ..deps import get_db
from ..domain.answer_parts import for_history, history_sources
from ..models import Conversation, QueuedMessage, Run, User
from ..service_auth import require_scope
from .routes_internal import _attachment_texts, _run_or_404, workspace_alive, workspace_data, workspace_key
from .routes_skills import skill_input_for
from .serializers import run_dict

router = APIRouter(prefix="/internal/runs", tags=["internal"])


@router.get("/{run_id}/model-input", dependencies=[Depends(require_scope("runs:read"))])
def model_input(run_id: str, db: Session = Depends(get_db)) -> dict:
    """Agent bootstrap without loading the complete conversation to find a workspace."""
    run = _run_or_404(db, run_id)
    out = run_dict(db, run, detail=True)
    conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    out["needs_conversation_title"] = bool(conversation and conversation.deleted_at is None and not conversation.title)
    from ..context_settings import context_settings

    out["context_settings"] = context_settings(db)
    out["history"] = []
    out["input_files"] = _attachment_texts(db, run)
    owner = db.get(User, run.user_id)
    from .routes_memory import memory_for_run

    out["memory"] = memory_for_run(owner, run)
    out["source_epoch"] = owner.context_source_epoch
    from .brand import workspace_input

    out.update(workspace_input(db, run))
    out.update(skill_input_for(db, owner))  # the same effective policy as the legacy internal GET
    path = ancestor_query(run.conversation_id, run.parent_run_id)
    newest = (
        db.scalar(
            select(Run)
            .join(path, Run.id == path.c.id)
            .where(or_(Run.result["workspace_saved"].as_boolean().is_(True), Run.runtime_meta["workspace"]["key"].as_string().is_not(None)))
            .order_by(path.c.depth)
            .limit(1)
        )
        if run.conversation_id and run.parent_run_id
        else None
    )
    from .source_lifecycle import invalid_history

    invalid = invalid_history(db, path_ids(db, run.conversation_id, run.parent_run_id)) if run.conversation_id else set()
    if run.conversation_id and run.parent_run_id:
        parent = db.get(Run, run.parent_run_id)
        if parent and parent.conversation_id == run.conversation_id and parent.user_id == run.user_id:
            if parent.id in invalid:
                out["context_boundary"] = "access_revoked"
            elif parent.model != run.model:
                out["context_boundary"] = "model_changed"
            elif parent.status == "succeeded" and (parent.runtime_meta or {}).get("context_snapshot"):
                from ..models import RunCheckpoint
                from .routes_runtime import read_checkpoint

                snapshot = read_checkpoint(db, parent) if db.get(RunCheckpoint, (parent.id, parent.checkpoint_step)) else None
                if snapshot:
                    state = snapshot["state"]
                    if state.get("finished") and state.get("messages") and state.get("tool_catalog_v1") is not None:
                        if state.get("context_model", parent.model) != run.model:
                            out["context_boundary"] = "model_changed"
                        else:
                            out["previous_model_context"] = {
                                "messages": state["messages"],
                                "tools": state["tool_catalog_v1"],
                                "delivered": state.get("delivered", {}),
                            }
                            if "memory_observed" in state:
                                out["previous_model_context"]["memory_observed"] = state["memory_observed"]
                else:
                    out["context_boundary"] = "rebuilt"
    alive = newest is not None and newest.id not in invalid and workspace_alive(newest, datetime.now(UTC))
    own = (run.runtime_meta or {}).get("workspace")
    out["workspace_restore_key"] = own["key"] if own else (workspace_data(newest).get("key") or workspace_key(newest.id)) if alive else None
    out["workspace_expired"] = newest is not None and not alive
    return out


@router.get("/{run_id}/model-context", dependencies=[Depends(require_scope("runs:read"))])
def model_context(run_id: str, after: str | None = None, db: Session = Depends(get_db)) -> dict:
    run = _run_or_404(db, run_id)
    conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    if not conversation:
        return {"summary": "", "through_run_id": None, "history": [], "has_more": False}
    ids = path_ids(db, conversation.id, run.parent_run_id)
    through = conversation.summary_through_run_id if conversation.summary_through_run_id in ids else None
    from .source_lifecycle import invalid_history

    invalid = invalid_history(db, ids)
    if through and through in invalid:
        through = None
    cursor_id = after or through
    start = 0
    if cursor_id:
        if cursor_id not in ids or (through and ids.index(cursor_id) < ids.index(through)):
            raise HTTPException(422, "invalid context cursor")
        start = ids.index(cursor_id) + 1
    page = ids[start : start + 100]
    rows = {
        r.id: r
        for r in db.execute(
            select(
                Run.id,
                Run.user_id,
                Run.input_file_ids,
                Run.prompt.label("prompt"),
                Run.options,
                Run.summary.label("summary"),
                Run.status,
                Run.runtime_meta["steer_ids"].label("steer_ids"),
                Run.runtime_meta["source_invalidated"].label("source_invalidated"),
                Run.result["sources"].label("sources"),
                func.substr(Run.error, 1, 16000).label("error"),
            ).where(Run.conversation_id == conversation.id, Run.id.in_(page))
        )
    }
    from .document_sources import history_manifests

    manifests = history_manifests(db, list(rows.values()))
    history, chars = [], 0
    for id_ in page:
        row = rows[id_]
        applied = row.steer_ids or []
        from ..agent.prompts import render_skill_mentions

        prompts = (
            {
                item.id: render_skill_mentions(item.prompt, (item.options or {}).get("skills"))
                for item in db.execute(
                    select(QueuedMessage.id, QueuedMessage.prompt, QueuedMessage.options).where(
                        QueuedMessage.run_id == id_, QueuedMessage.state == "steered"
                    )
                )
            }
            if applied
            else {}
        )
        steers = [prompts[qid] for qid in applied if qid in prompts]
        item = {
            "id": id_,
            "prompt": row.prompt,
            "skills": (row.options or {}).get("skills")
            or (
                [{"name": (row.options or {}).get("skill"), "title": (row.options or {}).get("skill_title")}]
                if (row.options or {}).get("skill")
                else []
            ),
            "reply": "（文件来源已失效，需重新读取）"
            if row.source_invalidated or id_ in invalid
            else for_history(row.summary or row.error or "", keep_ui=id_ == ids[-1], limit=16000),
            "status": row.status,
            "sources": history_sources(row.sources),
        }
        item["attachments"] = manifests.get(id_, [])
        if applied:
            from .document_sources import attachment_manifest

            for qid in applied:
                queued = db.get(QueuedMessage, qid)
                if queued and queued.run_id == id_ and queued.state == "steered":
                    item["attachments"].extend(
                        attachment_manifest(db, row, file_ids=queued.file_ids, message_id=qid, message_kind="queued_message")
                    )
        if steers:
            item["steers"] = steers
        size = len(item["prompt"]) + len(item["reply"]) + sum(map(len, steers))
        # Steers can make one turn much larger than its original prompt. Keep a
        # page near 256k characters, always making progress on a single large turn.
        if history and chars + size > 256000:
            break
        history.append(item)
        chars += size
    from ..models import ContextSummaryFrame
    from .summary_evidence import evidence

    frame = db.get(ContextSummaryFrame, conversation.summary_frame_id) if through and conversation.summary_frame_id else None
    revalidation = evidence(db, frame, run.user_id, set(ids) - invalid) if after is None else []
    return {
        "summary_evidence": revalidation,
        "summary_revision": frame.revision if frame else 0,
        "summary": (conversation.summary or "") if through else "",
        "source_epoch": db.get(User, run.user_id).context_source_epoch,
        "through_run_id": through,
        "history": history,
        "has_more": len(ids) > start + len(history),
    }


@router.put("/{run_id}/model-summary", dependencies=[Depends(require_scope("runs:status"))])
def save_model_summary(
    run_id: str, body: SummaryUpdate, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)
) -> dict:
    from ..locks import user_critical_section

    run = _run_or_404(db, run_id)
    with user_critical_section(db, run.user_id):
        return _save_model_summary(db, run, body, x_run_attempt)


def _save_model_summary(db, run, body, x_run_attempt):
    run_id = run.id
    from .runtime_fence import fence_attempt

    fence_attempt(db, run_id, x_run_attempt)
    ids = path_ids(db, run.conversation_id, run.parent_run_id) if run.conversation_id else []
    if body.through_run_id not in ids:
        raise HTTPException(422, "invalid summary boundary")
    if body.expected_through_run_id and (
        body.expected_through_run_id not in ids or ids.index(body.expected_through_run_id) >= ids.index(body.through_run_id)
    ):
        raise HTTPException(422, "summary boundary must advance")
    conversation = db.get(Conversation, run.conversation_id, populate_existing=True)
    owner = db.get(User, run.user_id, populate_existing=True)
    if body.source_epoch != owner.context_source_epoch:
        raise HTTPException(409, "context_source_changed")
    actual = conversation.summary_through_run_id
    effective = actual if actual in ids else None
    if effective != body.expected_through_run_id:
        raise HTTPException(422, "summary changed concurrently")
    changed = db.execute(
        update(Conversation)
        .where(
            Conversation.id == run.conversation_id,
            Conversation.summary_through_run_id == actual,
        )
        .values(summary=body.summary, summary_through_run_id=body.through_run_id)
    ).rowcount
    if not changed:
        raise HTTPException(422, "summary changed concurrently")
    import hashlib

    from ..models import ContextSummaryFrame, ContextSummarySource, MessageFileRef

    previous = db.get(ContextSummaryFrame, conversation.summary_frame_id) if conversation.summary_frame_id else None
    from .summary_evidence import advance

    try:
        revalidation = advance(db, previous, run.user_id, set(ids), body.revalidation_ids)
    except ValueError as exc:
        raise HTTPException(409, "revalidation_cursor_stale") from exc
    frame = ContextSummaryFrame(
        revalidation_state=revalidation,
        conversation_id=conversation.id,
        branch_anchor=run.parent_run_id,
        previous_id=previous.id if previous else None,
        revision=(previous.revision + 1) if previous else 1,
        from_run_id=body.expected_through_run_id,
        through_run_id=body.through_run_id,
        source_epoch=body.source_epoch,
        input_digest=hashlib.sha256((str(body.expected_through_run_id) + body.through_run_id).encode()).hexdigest(),
        summary=body.summary,
        status="active",
    )
    db.add(frame)
    db.flush()
    conversation.summary_frame_id = frame.id
    covered = ids[: ids.index(body.through_run_id) + 1]
    sources = set()
    if (run.options or {}).get("capability_discovery") == 1:
        from ..contracts.context_summary import ContextSummary
        from ..models import DocumentBlock, FileDerivation, FileObject

        try:
            semantic = ContextSummary.model_validate_json(body.summary)
        except ValueError as exc:
            raise HTTPException(422, "invalid summary schema") from exc
        allowed = set(db.scalars(select(MessageFileRef.file_id).where(MessageFileRef.run_id.in_(covered))))
        for source in semantic.sources():
            if source.run_id and source.run_id not in covered:
                raise HTTPException(422, "invalid summary run source")
            if source.file_id:
                if source.file_id not in allowed:
                    raise HTTPException(422, "invalid summary file source")
                file = db.get(FileObject, source.file_id, populate_existing=True)
                if not file or file.user_id != run.user_id or file.deleted_at or file.purged_at:
                    raise HTTPException(409, "summary source unavailable")
                sources.add(source.file_id)
            if source.block_id:
                fid = db.scalar(
                    select(FileDerivation.file_id)
                    .join(DocumentBlock, DocumentBlock.derivation_id == FileDerivation.id)
                    .where(DocumentBlock.id == source.block_id)
                )
                if not fid or fid not in allowed or (source.file_id and fid != source.file_id):
                    raise HTTPException(422, "invalid summary block source")
                sources.add(fid)
            if source.run_id:
                sources.update(db.scalars(select(MessageFileRef.file_id).where(MessageFileRef.run_id == source.run_id)))
    else:
        sources.update(db.scalars(select(MessageFileRef.file_id).where(MessageFileRef.run_id.in_(covered))))
        for legacy in db.scalars(select(Run.input_file_ids).where(Run.id.in_(covered))):
            sources.update(legacy or [])
    for file_id in sources:
        db.add(ContextSummarySource(frame_id=frame.id, file_id=file_id))
    from sqlalchemy import delete

    stale = list(
        db.scalars(
            select(ContextSummaryFrame.id)
            .where(ContextSummaryFrame.conversation_id == conversation.id)
            .order_by(ContextSummaryFrame.created_at.desc(), ContextSummaryFrame.id.desc())
            .offset(20)
            .limit(100)
        )
    )
    if stale:
        db.execute(delete(ContextSummarySource).where(ContextSummarySource.frame_id.in_(stale)))
        db.execute(delete(ContextSummaryFrame).where(ContextSummaryFrame.id.in_(stale)))
    db.commit()
    return {"ok": True}
