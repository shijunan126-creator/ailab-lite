"""Bounded model history pages and API-owned summary memory."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..contracts.model_context import SummaryUpdate
from ..conversation_tree import ancestor_query, path_ids
from ..deps import get_db
from ..models import Conversation, Run, User
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
    from .routes_memory import memory_for

    out["memory"] = memory_for(owner, for_model=True) if owner else {}
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
    alive = newest is not None and workspace_alive(newest, datetime.now(UTC))
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
                func.substr(Run.prompt, 1, 16000).label("prompt"),
                func.substr(Run.summary, 1, 16000).label("summary"),
                Run.status,
                func.substr(Run.error, 1, 16000).label("error"),
            ).where(Run.conversation_id == conversation.id, Run.id.in_(page))
        )
    }
    return {
        "summary": (conversation.summary or "") if through else "",
        "through_run_id": through,
        "history": [
            {"id": id_, "prompt": rows[id_].prompt, "reply": rows[id_].summary or rows[id_].error or "", "status": rows[id_].status}
            for id_ in page
        ],
        "has_more": len(ids) > start + 100,
    }


@router.put("/{run_id}/model-summary", dependencies=[Depends(require_scope("runs:status"))])
def save_model_summary(
    run_id: str, body: SummaryUpdate, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)
) -> dict:
    run = _run_or_404(db, run_id)
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
    db.commit()
    return {"ok": True}
