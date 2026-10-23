"""API-owned runtime snapshots, pauses and transactional continuation."""

import gzip
import io
import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import quota
from ..config import settings
from ..deps import current_user, get_db
from ..events import append_event
from ..library import schedule_object_deletion
from ..locks import user_critical_section
from ..models import Conversation, Run, RunAttempt, RunCheckpoint, RunStatus, User
from ..run_state import transition_run
from ..schemas import RunDetail
from ..service_auth import require_scope
from ..storage import get_storage
from .jobs import stage_job
from .routes_runs import _get_owned_run
from .runtime_fence import fence_attempt
from .serializers import run_dict

router = APIRouter(tags=["runtime"])


class CheckpointIn(BaseModel):
    expected_step: int = Field(ge=0)
    state: dict


def read_checkpoint(db, run):
    row = db.get(RunCheckpoint, (run.id, run.checkpoint_step))
    if row is None:
        if run.checkpoint_step:
            raise HTTPException(409, "checkpoint_missing")
        return None
    try:
        # Bound decompression as well as the encoded request. No zip bombs.
        with get_storage().open_stream(row.state_key) as chunks:
            compressed = io.BytesIO()
            for chunk in chunks:
                if compressed.tell() + len(chunk) > settings.checkpoint_max_bytes:
                    raise ValueError("checkpoint too large")
                compressed.write(chunk)
        compressed.seek(0)
        with gzip.GzipFile(fileobj=compressed) as source:
            raw = source.read(settings.checkpoint_max_bytes + 1)
        if len(raw) > settings.checkpoint_max_bytes:
            raise ValueError("checkpoint too large")
        state = json.loads(raw)
        if not isinstance(state, dict) or state.get("runtime_version") != 1:
            raise ValueError("unsupported runtime state")
        return {"step": row.step, "state": state, "answer": (run.runtime_meta or {}).get("answer")}
    except Exception as exc:
        raise HTTPException(409, "checkpoint_unavailable") from exc


@router.get("/internal/runs/{run_id}/checkpoint", dependencies=[Depends(require_scope("runs:read"))])
def get_checkpoint(run_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return read_checkpoint(db, run)


@router.put("/internal/runs/{run_id}/checkpoint", dependencies=[Depends(require_scope("runs:status"))])
def put_checkpoint(run_id: str, body: CheckpointIn, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    raw = json.dumps(body.state, ensure_ascii=False).encode()
    if len(raw) > settings.checkpoint_max_bytes or body.state.get("runtime_version") != 1:
        raise HTTPException(413, "checkpoint exceeds limit or unsupported version")
    step = body.expected_step + 1
    key = f"checkpoints/{run_id}/{step}-{uuid4().hex}.json.gz"
    storage = get_storage()
    with user_critical_section(db, run.user_id):
        fence_attempt(db, run_id, x_run_attempt)
        db.refresh(run)
        if run.status != RunStatus.RUNNING:
            raise HTTPException(409, "run_not_running")
        won = db.execute(
            update(Run).where(Run.id == run_id, Run.checkpoint_step == body.expected_step).values(checkpoint_step=step)
        ).rowcount
        if not won:
            raise HTTPException(409, "stale_step")
        # Register the immutable object first. Failed transactions leave a bounded
        # orphan, collected after the grace period; never overwrite a winning CAS.
        storage.put_bytes(key, gzip.compress(raw), "application/gzip")
        db.add(
            RunCheckpoint(
                run_id=run_id,
                step=step,
                attempt_id=x_run_attempt,
                state_key=key,
                plan=body.state.get("plan", []),
                tool_log={k: {"name": v["name"], "completed": "result" in v} for k, v in body.state.get("tool_log", {}).items()},
                deliveries=body.state.get("delivered", {}),
                runtime_version=1,
            )
        )
        workspace = body.state.get("workspace")
        if workspace and not str(workspace.get("key", "")).startswith(f"workspaces/{run_id}/"):
            raise HTTPException(422, "invalid workspace key")
        run.runtime_meta = {**(run.runtime_meta or {}), "plan": body.state.get("plan", []), "workspace": workspace}
        old = db.scalars(select(RunCheckpoint).where(RunCheckpoint.run_id == run_id, RunCheckpoint.step < step - 1)).all()
        for row in old:
            schedule_object_deletion(db, row.state_key, "checkpoint")
            db.delete(row)
        db.commit()
    return {"step": step}


class PauseIn(BaseModel):
    status: str
    question: dict | None = None
    error: str = Field(default="", max_length=2000)
    auto_resume: bool = False


def queue_continuation(db, run):
    # A continuation is a new unit of work. An old ACK can only delete its old job.
    job_id = uuid4().hex
    meta = dict(run.runtime_meta or {})
    meta["job_id"] = job_id
    run.runtime_meta = meta
    db.execute(
        update(RunAttempt)
        .where(RunAttempt.run_id == run.id, RunAttempt.ended_at.is_(None))
        .values(ended_at=datetime.now(UTC), outcome="continued")
    )
    transition_run(db, run.id, RunStatus.QUEUED, commit=False, error="")
    stage_job(db, "run", run_id=run.id, mode=(run.options or {}).get("mode", "work"), job_id=job_id)
    append_event(db, run.id, "run.resumed", {"from_step": run.checkpoint_step}, commit=False)


@router.post("/internal/runs/{run_id}/pause", dependencies=[Depends(require_scope("runs:status"))])
def pause_run(run_id: str, body: PauseIn, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    with user_critical_section(db, run.user_id):
        fence_attempt(db, run_id, x_run_attempt)
        db.refresh(run)
        if body.status not in {RunStatus.WAITING_USER, RunStatus.INTERRUPTED} or not run.checkpoint_step:
            raise HTTPException(409, "safe_checkpoint_required")
        meta = dict(run.runtime_meta or {})
        run.waiting_since = datetime.now(UTC)  # starts the user-facing timeout for waiting_user and interrupted alike
        if body.status == RunStatus.WAITING_USER:
            q = body.question or {}
            if not isinstance(q.get("question"), str) or not q.get("question_id"):
                raise HTTPException(422, "question required")
            meta.update(question={**q, "created_at": datetime.now(UTC).isoformat()}, answer=None)
            append_event(db, run.id, "agent.ask_user", q, commit=False)
        run.runtime_meta = meta
        transition_run(db, run.id, body.status, error=body.error, commit=False)
        db.execute(
            update(RunAttempt)
            .where(RunAttempt.id == x_run_attempt, RunAttempt.ended_at.is_(None))
            .values(ended_at=datetime.now(UTC), outcome=body.status)
        )
        if body.auto_resume and body.status == RunStatus.INTERRUPTED and meta.get("auto_resumes", 0) < 1:
            run.runtime_meta = {**meta, "auto_resumes": 1}
            queue_continuation(db, run)
        db.commit()
    return {"status": run.status}


class AnswerIn(BaseModel):
    question_id: str = Field(min_length=1, max_length=160)
    answer: str = Field(min_length=1, max_length=16000)


def _admit_continuation(db, run):
    """The paused run released its slot; continuing acquires one under the same user lock as creation."""
    quota.enforce_token_quota(db, run.user_id)
    active = db.scalar(select(func.count()).select_from(Run).where(Run.user_id == run.user_id, Run.status.in_(RunStatus.ACTIVE)))
    if active >= quota.user_limits(db, run.user_id).max_concurrent_runs:
        raise HTTPException(429, "too many active runs for this user")
    if run.conversation_id:
        conversation = db.get(Conversation, run.conversation_id, populate_existing=True)
        if conversation is None or conversation.deleted_at is not None:
            raise HTTPException(404, "conversation not found")
        busy = db.scalar(
            select(Run.id).where(Run.conversation_id == run.conversation_id, Run.id != run.id, Run.status.in_(RunStatus.ACTIVE)).limit(1)
        )
        if busy:
            raise HTTPException(409, "this conversation is still answering the previous message")
    read_checkpoint(db, run)


@router.post("/v1/runs/{run_id}/answer", response_model=RunDetail, status_code=202)
def answer_run(run_id: str, body: AnswerIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = _get_owned_run(db, run_id, user)
    with user_critical_section(db, user.id):
        db.refresh(run)
        meta = run.runtime_meta or {}
        question = meta.get("question") or {}
        if (
            run.status not in {RunStatus.WAITING_USER, RunStatus.INTERRUPTED}
            or question.get("question_id") != body.question_id
            or meta.get("answer")
        ):
            raise HTTPException(409, "question_already_answered_or_inactive")
        if not question.get("allow_text", True) and body.answer not in question.get("options", []):
            raise HTTPException(422, "answer must be an offered option")
        _admit_continuation(db, run)
        # The answer names its question, so the Agent can never apply it to a different one.
        run.runtime_meta = {**meta, "answer": {"question_id": body.question_id, "answer": body.answer}, "auto_resumes": 0}
        append_event(db, run.id, "agent.ask_answered", body.model_dump(), commit=False)
        queue_continuation(db, run)
        db.commit()
    return run_dict(db, run, detail=True)


@router.post("/v1/runs/{run_id}/resume", response_model=RunDetail)
def resume_run(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = _get_owned_run(db, run_id, user)
    with user_critical_section(db, user.id):
        db.refresh(run)
        meta = run.runtime_meta or {}
        if run.status != RunStatus.INTERRUPTED or not run.checkpoint_step or (meta.get("question") and not meta.get("answer")):
            raise HTTPException(409, "run_not_resumable")
        _admit_continuation(db, run)
        # A person deciding to continue renews the one automatic continuation a lease loss gets.
        run.runtime_meta = {**meta, "auto_resumes": 0}
        queue_continuation(db, run)
        db.commit()
    return run_dict(db, run, detail=True)
