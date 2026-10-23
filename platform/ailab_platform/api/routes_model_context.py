"""Bounded model history pages and API-owned summary memory."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select, tuple_, update
from sqlalchemy.orm import Session

from ..contracts.model_context import SummaryUpdate
from ..deps import get_db
from ..models import Conversation, Run, User
from ..service_auth import require_scope
from .routes_internal import _attachment_texts, _run_or_404, workspace_alive, workspace_key
from .routes_skills import skill_input_for
from .serializers import run_dict

router = APIRouter(prefix="/internal/runs", tags=["internal"])


def preceding(cursor):
    return tuple_(Run.created_at, Run.id) < (cursor.created_at, cursor.id)


@router.get("/{run_id}/model-input", dependencies=[Depends(require_scope("runs:read"))])
def model_input(run_id: str, db: Session = Depends(get_db)) -> dict:
    """Agent bootstrap without loading the complete conversation to find a workspace."""
    run = _run_or_404(db, run_id)
    out = run_dict(db, run, detail=True)
    conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    out["needs_conversation_title"] = bool(conversation and conversation.deleted_at is None and not conversation.title)
    out["history"] = []
    out["input_files"] = _attachment_texts(db, run)
    owner = db.get(User, run.user_id)
    out.update(skill_input_for(db, owner))  # the same effective policy as the legacy internal GET
    newest = (
        db.scalar(
            select(Run)
            .where(
                Run.conversation_id == run.conversation_id,
                preceding(run),
                Run.result["workspace_saved"].as_boolean().is_(True),
            )
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(1)
        )
        if run.conversation_id
        else None
    )
    alive = newest is not None and workspace_alive(newest, datetime.now(UTC))
    own = (run.runtime_meta or {}).get("workspace")
    out["workspace_restore_key"] = (
        own["key"] if own else ((newest.result or {}).get("workspace_key") or workspace_key(newest.id)) if alive else None
    )
    out["workspace_expired"] = newest is not None and not alive
    return out


@router.get("/{run_id}/model-context", dependencies=[Depends(require_scope("runs:read"))])
def model_context(run_id: str, after: str | None = None, db: Session = Depends(get_db)) -> dict:
    run = _run_or_404(db, run_id)
    conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    if not conversation:
        return {"summary": "", "through_run_id": None, "history": [], "has_more": False}
    query = select(
        Run.id,
        func.substr(Run.prompt, 1, 16000).label("prompt"),
        func.substr(Run.summary, 1, 16000).label("summary"),
        Run.status,
        func.substr(Run.error, 1, 16000).label("error"),
    ).where(Run.conversation_id == conversation.id, preceding(run))
    cursor_id = after or conversation.summary_through_run_id
    if cursor_id:
        cursor = _run_or_404(db, cursor_id)
        if cursor.conversation_id != conversation.id or (cursor.created_at, cursor.id) >= (run.created_at, run.id):
            raise HTTPException(409, "invalid context cursor")
        query = query.where(tuple_(Run.created_at, Run.id) > (cursor.created_at, cursor.id))
    rows = list(db.execute(query.order_by(Run.created_at, Run.id).limit(101)))
    return {
        "summary": conversation.summary or "",
        "through_run_id": conversation.summary_through_run_id,
        "history": [
            {"id": r.id, "prompt": r.prompt[:16000], "reply": (r.summary or r.error or "")[:16000], "status": r.status} for r in rows[:100]
        ],
        "has_more": len(rows) > 100,
    }


@router.put("/{run_id}/model-summary", dependencies=[Depends(require_scope("runs:status"))])
def save_model_summary(
    run_id: str, body: SummaryUpdate, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)
) -> dict:
    run = _run_or_404(db, run_id)
    from .runtime_fence import fence_attempt

    fence_attempt(db, run_id, x_run_attempt)
    through = _run_or_404(db, body.through_run_id)
    if (
        not run.conversation_id
        or through.conversation_id != run.conversation_id
        or (through.created_at, through.id) >= (run.created_at, run.id)
    ):
        raise HTTPException(409, "invalid summary boundary")
    previous = _run_or_404(db, body.expected_through_run_id) if body.expected_through_run_id else None
    if previous and (
        previous.conversation_id != run.conversation_id or (previous.created_at, previous.id) >= (through.created_at, through.id)
    ):
        raise HTTPException(409, "summary boundary must advance")
    changed = db.execute(
        update(Conversation)
        .where(
            Conversation.id == run.conversation_id,
            Conversation.summary_through_run_id == body.expected_through_run_id,
        )
        .values(summary=body.summary, summary_through_run_id=through.id)
    ).rowcount
    if not changed:
        raise HTTPException(409, "summary changed concurrently")
    db.commit()
    return {"ok": True}
