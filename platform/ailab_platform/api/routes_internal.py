"""Internal service-to-service API.

Every route requires ``X-Service-Token``. Agent, Broker and Worker use these
endpoints instead of touching the database or object storage ledger directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..agent.prompts import skills as builtin_skills
from ..config import settings
from ..contracts.file_preview import PreviewCompletion
from ..contracts.usage import ReservationOut, ReserveUsageIn, SettleUsageIn, UsageErrorCode, UsageOut
from ..conversation_titles import apply_result_title
from ..conversation_tree import path_ids
from ..deps import get_db
from ..events import append_event
from ..library import assign_file_version, delete_storage_objects, reserve_filename, thumbnail_candidate, trim_file_versions
from ..locks import user_critical_section
from ..models import (
    Artifact,
    Conversation,
    FileObject,
    FileStatus,
    OutboxMessage,
    Project,
    QueuedMessage,
    Run,
    RunAttempt,
    RunDelivery,
    RunStatus,
    UsageReservation,
    User,
    new_id,
)
from ..ports.scanner import get_scanner
from ..ports.service_auth import RunContext
from ..quota import enforce_library_quota, reserve_usage, run_usage_totals, settle_usage
from ..run_state import InvalidTransition, transition_run
from ..schemas import (
    ArtifactIn,
    CommitOut,
    ConversationTitle,
    EventIn,
    EventOut,
    FileOut,
    FilePatch,
    InternalRunDetail,
    RunDetail,
    RunStateOut,
    RunStatusIn,
    RunTransitionIn,
)
from ..service_auth import ServicePrincipal, require_scope
from ..storage import get_storage
from ..title_text import normalize_title
from .inbox import pending_query
from .jobs import stage_job
from .routes_ocr import request_ocr
from .routes_skills import skill_input_for, usable_personal
from .runtime_fence import fenced_run
from .serializers import file_dict, run_dict

router = APIRouter(prefix="/internal", tags=["internal"])
log = logging.getLogger("ailab.api.internal")
# Statuses an Agent may write through /transition. Others are reached through their own, checked, endpoints.
AGENT_STATUSES = frozenset({RunStatus.RUNNING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED})


def _run_or_404(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


ATTACHMENT_CHARS = 30_000  # of one attachment's text given to the model
ATTACHMENTS_CHARS = 60_000  # of all of a run's attachments together


def _attachment_texts(db: Session, run: Run) -> list[dict]:
    """The run's input files, each with the text the Worker extracted (so the model can read it without a sandbox)."""
    storage = get_storage()
    out, budget = [], ATTACHMENTS_CHARS
    for file_id in run.input_file_ids or []:
        f = db.get(FileObject, file_id)
        if f is None or f.deleted_at is not None or f.purged_at is not None:
            continue
        meta = f.meta or {}
        keys = [meta.get("text_key"), meta.get("ocr_text_key") if meta.get("ocr") == "done" else None]
        text = ""
        for key in filter(None, keys):  # native extraction and OCR text of scanned pages sit side by side
            try:
                text = "\n".join(filter(None, [text, storage.get_bytes(key).decode("utf-8", "replace")]))
            except Exception:
                log.warning("attachment text missing for %s", file_id, exc_info=True)
        total = len(text) or int((meta.get("extract") or {}).get("chars") or 0)
        if not text and meta.get("excerpt"):  # processed before full texts were kept: the excerpt is all there is
            text = meta["excerpt"]
        keep = text[: max(0, min(ATTACHMENT_CHARS, budget))]
        budget -= len(keep)
        out.append(
            {
                "file_id": f.id,
                "id": f.id,
                "content_type": f.content_type,
                "meta": {"ocr": meta.get("ocr", "unavailable")},
                "filename": f.filename,
                "kind": (meta.get("extract") or {}).get("kind", ""),
                "chars": total,
                "text": keep,
                "truncated": len(keep) < total,
            }
        )
    return out


HISTORY_TURNS = 12
HISTORY_CHARS = 4000  # per reply: older answers are context, not something to repeat verbatim


def workspace_key(run_id: str) -> str:
    """Where the Broker keeps the sandbox workspace a turn left behind (derived, never taken from a caller)."""
    return f"workspaces/{run_id}.tgz"


def workspace_data(run: Run) -> dict:
    result = run.result or {}
    if result.get("workspace_saved"):
        return {"key": result.get("workspace_key"), "saved_at": result.get("workspace_saved_at")}
    return (run.runtime_meta or {}).get("workspace") or {}


def workspace_alive(run: Run, now: datetime) -> bool:
    """Snapshot eligibility matches the object-store TTL; checkpoint snapshots cover failed/canceled turns too."""
    snapshot = workspace_data(run)
    if not snapshot:
        return False
    saved_at, when = snapshot.get("saved_at"), None
    if isinstance(saved_at, int | float):
        when = datetime.fromtimestamp(saved_at, UTC)
    elif isinstance(saved_at, str):
        try:
            when = datetime.fromisoformat(saved_at)
        except ValueError:
            pass
    when = when or run.finished_at or run.created_at
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return settings.workspace_ttl_days <= 0 or when + timedelta(days=settings.workspace_ttl_days) > now


def _earlier_turns(db: Session, run: Run) -> list[Run]:
    from ..conversation_tree import path_runs

    return path_runs(db, run.conversation_id, run.parent_run_id) if run.conversation_id else []


@router.get("/runs/{run_id}", response_model=InternalRunDetail, dependencies=[Depends(require_scope("runs:read"))])
def internal_get_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    """The run, and for a conversation turn: the earlier turns (for the model) and the workspace to restore (for the Broker)."""
    run = _run_or_404(db, run_id)
    out = run_dict(db, run, detail=True)
    out["budget_window_id"] = (run.runtime_meta or {}).get("budget_window_id", (run.runtime_meta or {}).get("job_id", run.id))
    earlier = _earlier_turns(db, run)
    history = []
    for turn in earlier[-HISTORY_TURNS:]:
        reply = turn.summary or (f"（这一轮失败了：{turn.error}）" if turn.error else "")
        if turn.status == RunStatus.CANCELED:
            reply = (reply + "\n" if reply else "") + "（用户中止了这一轮）"
        files = [
            f.filename
            for f in db.scalars(select(FileObject).join(Artifact, Artifact.file_id == FileObject.id).where(Artifact.run_id == turn.id))
        ]
        from .inbox import steers_for_run

        applied = (turn.runtime_meta or {}).get("steer_ids", [])
        steers = [row.prompt for row in steers_for_run(db, turn) if row.id in applied]
        history.append(
            {
                **({"steers": steers} if steers else {}),
                "prompt": turn.prompt,
                "reply": reply[:HISTORY_CHARS],
                "status": turn.status,
                "files": files,
            }
        )
    conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    out["needs_conversation_title"] = bool(conversation and conversation.deleted_at is None and not conversation.title)
    out["project_library"] = []
    if conversation and conversation.project_id:
        out["project_library"] = [
            {"file_id": f.id, "filename": f.filename[:200]}
            for f in db.scalars(
                select(FileObject)
                .where(
                    FileObject.user_id == run.user_id,
                    FileObject.project_id == conversation.project_id,
                    FileObject.status == FileStatus.READY,
                    FileObject.purpose == "library",
                    FileObject.is_head == 1,
                    FileObject.deleted_at.is_(None),
                    FileObject.purged_at.is_(None),
                )
                .order_by(FileObject.created_at.desc(), FileObject.id.desc())
                .limit(50)
            )
        ]
    out["history"] = history
    out["input_files"] = _attachment_texts(db, run)
    owner = db.get(User, run.user_id)
    from .routes_memory import memory_for

    out["memory"] = memory_for(owner, for_model=True) if owner else {}
    from .brand import workspace_input

    out.update(workspace_input(db, run))
    out.update(skill_input_for(db, owner))
    now = datetime.now(UTC)
    newest = next((t for t in reversed(earlier) if workspace_data(t)), None)
    alive = newest is not None and workspace_alive(newest, now)
    own = (run.runtime_meta or {}).get("workspace")
    out["workspace_restore_key"] = own["key"] if own else (workspace_data(newest).get("key") or workspace_key(newest.id)) if alive else None
    # the last turn left files, but they are past the retention window: tell the model instead of letting it hunt
    out["workspace_expired"] = newest is not None and not alive
    return out


@router.get("/runs/{run_id}/state", response_model=RunStateOut, dependencies=[Depends(require_scope("runs:read"))])
def internal_run_state(run_id: str, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)) -> dict:
    """The cheap status poll the Agent uses while a command/model call is running."""
    run = _run_or_404(db, run_id)
    if x_run_attempt:
        # A read-only ownership probe cancels in-flight model calls promptly on lease loss.
        owned = db.scalar(
            select(RunAttempt.id)
            .join(OutboxMessage, OutboxMessage.claim_token == RunAttempt.id)
            .where(
                RunAttempt.id == x_run_attempt,
                RunAttempt.run_id == run_id,
                RunAttempt.ended_at.is_(None),
                OutboxMessage.claimed_until > datetime.now(UTC),
            )
        )
        if not owned:
            raise HTTPException(409, "stale_attempt")
    pending = False
    if run.status == RunStatus.RUNNING:
        applied = (run.runtime_meta or {}).get("steer_ids", [])
        pending = bool(
            db.scalar(
                select(QueuedMessage.id)
                .where(QueuedMessage.run_id == run.id, QueuedMessage.state == "steered", QueuedMessage.id.not_in(applied))
                .limit(1)
            )
        )
        if not pending and run.conversation_id:
            conv = db.get(Conversation, run.conversation_id)
            if conv and conv.active_run_id == run.id and not conv.queue_paused:
                pending = bool(
                    db.scalar(pending_query(conv.id).where(QueuedMessage.steer.is_(True)).with_only_columns(QueuedMessage.id).limit(1))
                )
    return {
        "steer_pending": pending,
        "status": run.status,
        "finished_at": run.finished_at,
        "job_id": (run.runtime_meta or {}).get("job_id", run.id),
    }


@router.post("/runs/{run_id}/status", response_model=RunDetail, dependencies=[Depends(require_scope("runs:status"))])
def internal_set_status(
    run_id: str, body: RunStatusIn, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)
) -> dict:
    # Legacy spelling shares exactly the same fence and transaction.
    return internal_transition(run_id, RunTransitionIn(**body.model_dump()), x_run_attempt, db)


@router.post("/runs/{run_id}/transition", response_model=RunDetail, dependencies=[Depends(require_scope("runs:status"))])
def internal_transition(
    run_id: str, body: RunTransitionIn, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)
) -> dict:
    # waiting_user and interrupted need a question or a safe checkpoint, so only /pause may set them; queued only via the API.
    if body.status not in AGENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"bad status: {body.status}")
    run = _run_or_404(db, run_id)
    with fenced_run(db, run, x_run_attempt or body.attempt_id) as run:
        try:
            result = body.result
            if result is not None:
                # Failure output is partial (for example sources collected before a crash).
                # Preserve previously saved progress under the same ownership lock.
                if body.status == RunStatus.FAILED:
                    result = {**(run.result or {}), **result}
                result = {**result, "usage_totals": run_usage_totals(db, run_id)}
            run, changed = transition_run(db, run_id, body.status, summary=body.summary, error=body.error, result=result, commit=False)
            if changed:
                for event in body.events:
                    append_event(db, run_id, event.type, event.payload, commit=False)
                if run.status == RunStatus.SUCCEEDED:
                    try:
                        with db.begin_nested():
                            apply_result_title(db, run)
                    except SQLAlchemyError:
                        logging.getLogger("ailab.api.titles").warning("automatic title update failed")
            db.commit()
        except InvalidTransition as exc:
            raise HTTPException(409, f"invalid transition: {exc}") from exc
    return run_dict(db, run, detail=True)


@router.post("/runs/{run_id}/events", response_model=EventOut, dependencies=[Depends(require_scope("runs:events"))])
def internal_append_event(
    run_id: str, body: EventIn, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)
) -> dict:
    run = _run_or_404(db, run_id)
    with fenced_run(db, run, x_run_attempt):
        event = append_event(db, run_id, body.type, body.payload)
    return {
        "seq": event.seq,
        "type": event.type,
        "payload": event.payload,
        "created_at": event.created_at,
    }


@router.post("/runs/{run_id}/usage", dependencies=[Depends(require_scope("usage:write"))])
def internal_usage_retired(run_id: str) -> None:
    raise HTTPException(410, {"code": UsageErrorCode.RESERVATION_REQUIRED})


@router.post("/usage/reserve", response_model=ReservationOut, dependencies=[Depends(require_scope("usage:reserve"))])
def internal_reserve_usage(body: ReserveUsageIn, db: Session = Depends(get_db)) -> dict:
    if body.subject != "run":
        raise HTTPException(403, "file usage is reserved by the OCR service")
    row = reserve_usage(db, body)
    return {
        "call_id": row.call_id,
        "run_id": row.run_id,
        "subject": row.subject,
        "file_id": row.file_id,
        "attempt_id": row.attempt_id,
        "status": row.status,
        "reserved_amount": row.reserved_amount,
        "weight": str(row.weight),
        "cached_token_weight": str(row.cached_token_weight),
        "policy_version": row.policy_version,
        "expires_at": row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at.astimezone(UTC),
    }


@router.post("/usage/settle", response_model=UsageOut, dependencies=[Depends(require_scope("usage:write"))])
def internal_settle_usage(body: SettleUsageIn, db: Session = Depends(get_db)) -> dict:
    reservation = db.get(UsageReservation, body.call_id)
    if reservation is not None:
        if reservation.subject != "run":
            raise HTTPException(403, "file usage is settled by the OCR service")
        if reservation.attempt_id != body.attempt_id:
            raise HTTPException(409, "call_identity_mismatch")
    row = settle_usage(db, body)
    totals = run_usage_totals(db, row.run_id)
    return {
        "call_id": row.call_id,
        "run_id": row.run_id,
        "subject": row.subject,
        "file_id": row.file_id,
        "attempt_id": row.attempt_id,
        "mode": row.mode,
        "alias": row.alias,
        "actual_backend": row.actual_backend,
        "input_tokens": row.prompt_tokens,
        "cached_tokens": row.cached_tokens,
        "output_tokens": row.completion_tokens,
        "reasoning_tokens": row.reasoning_tokens,
        "weighted_amount": row.weighted_amount,
        "policy_version": row.policy_version,
        "status": row.status,
        "run_tokens": totals["prompt_tokens"] + totals["completion_tokens"],
    }


@router.get("/runs/{run_id}/usage", dependencies=[Depends(require_scope("runs:read"))])
def internal_run_usage(run_id: str, db: Session = Depends(get_db)) -> dict:
    _run_or_404(db, run_id)
    return run_usage_totals(db, run_id)


def _artifact_out(artifact: Artifact, file: FileObject) -> dict:
    return {
        "artifact_id": artifact.id,
        "file_id": file.id,
        "filename": file.filename,
        "content_type": file.content_type,
        "size": file.size,
        "sha256": file.sha256,
        "download_url": f"/v1/files/{file.id}/content",
    }


def _artifact_filename(name: str) -> str:
    name = PurePosixPath(name.replace("\\", "/")).name.strip()
    return name[:200] if name not in ("", ".", "..") else "artifact"


@router.post("/artifacts", response_model=CommitOut, dependencies=[Depends(require_scope("artifacts:commit_for_run"))])
def internal_commit_artifact(
    body: ArtifactIn,
    x_run_attempt: str | None = Header(default=None),
    x_delivery_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Second half of the two-phase delivery. Safe to retry.

    Same (run, filename, sha256) returns the artifact that already exists instead
    of creating a duplicate, and the staging object is only removed after the
    database row is committed, so a failure in between can be retried.
    """
    run = _run_or_404(db, body.run_id)
    with fenced_run(db, run, x_run_attempt) as run:
        if not x_delivery_id or not x_delivery_id.startswith(run.id + ":") or len(x_delivery_id) > 160:
            raise HTTPException(422, "stable delivery identity required")
        if not body.staging_key.startswith("staging/") or ".." in body.staging_key.split("/"):
            raise HTTPException(400, "staging_key must be under staging/")
        prior = db.get(RunDelivery, (run.id, x_delivery_id))
        if prior:
            get_storage().delete(body.staging_key)
            db.commit()
            return prior.result
        return _commit_artifact(body, db, run, x_delivery_id)


def _commit_artifact(body: ArtifactIn, db: Session, run: Run, delivery_id: str) -> dict:
    if not body.staging_key.startswith("staging/"):
        raise HTTPException(status_code=400, detail="staging_key must be under staging/")
    storage = get_storage()

    def reject(code: int, detail: str) -> HTTPException:
        # Nobody will ever look at this staging object again; don't leak it.
        storage.delete(body.staging_key)
        return HTTPException(status_code=code, detail=detail)

    if run.status != RunStatus.RUNNING:
        raise reject(409, f"run is {run.status}; artifacts are only accepted while it runs")
    if body.size > settings.max_artifact_bytes:
        raise reject(413, f"artifact exceeds {settings.max_artifact_bytes} bytes")
    filename = _artifact_filename(body.filename)

    existing = db.execute(
        select(Artifact, FileObject)
        .join(FileObject, Artifact.file_id == FileObject.id)
        .where(Artifact.run_id == run.id, FileObject.filename == filename, FileObject.sha256 == body.sha256)
    ).first()
    if existing is not None:
        storage.delete(body.staging_key)
        _enqueue_thumbnail(db, existing[1])
        db.add(RunDelivery(run_id=run.id, delivery_id=delivery_id, result=_artifact_out(*existing)))
        db.commit()
        return _artifact_out(*existing)

    try:
        enforce_library_quota(db, run.user_id, body.size)
    except HTTPException as exc:
        raise reject(exc.status_code, exc.detail) from exc

    if not storage.exists(body.staging_key):
        raise HTTPException(status_code=409, detail="staging object not found")
    actual_size = storage.size(body.staging_key)
    if actual_size != body.size:
        raise reject(400, f"size mismatch: got {actual_size} expected {body.size}")
    # Hash and scan the staged object in one pass. The spool cap keeps a large
    # artifact out of the API heap while still giving clamd a readable stream.
    hasher = hashlib.sha256()
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as scanner_input:
        for chunk in storage.stream(body.staging_key):
            hasher.update(chunk)
            scanner_input.write(chunk)
        digest = hasher.hexdigest()
        if digest != body.sha256:
            raise reject(400, "sha256 mismatch")
        scanner_input.seek(0)
        try:
            scan = get_scanner().scan(scanner_input)
        except Exception as exc:
            raise reject(503, f"artifact scanner unavailable: {type(exc).__name__}") from exc
        if scan.verdict == "infected":
            raise reject(400, f"artifact rejected by scanner: {scan.signature or 'malware'}")
        if scan.verdict != "clean":
            raise reject(503, "artifact scanner unavailable")

    file_id = new_id("fil")
    final_key = f"runs/{run.id}/{file_id}/{filename}"
    storage.copy(body.staging_key, final_key)

    conversation = db.get(Conversation, run.conversation_id, populate_existing=True) if run.conversation_id else None
    project_id = conversation.project_id if conversation is not None else None
    if project_id is not None and db.get(Project, project_id) is None:
        project_id = None
    file = FileObject(
        id=file_id,
        user_id=run.user_id,
        project_id=project_id,
        filename=filename,
        content_type=body.content_type,
        size=actual_size,
        sha256=digest,
        storage_key=final_key,
        status=FileStatus.READY,
        meta={"source": "sandbox", "run_id": run.id},
    )
    parent = None
    if run.conversation_id:
        parent = db.scalar(
            select(FileObject)
            .join(Artifact, Artifact.file_id == FileObject.id)
            .join(Run, Run.id == Artifact.run_id)
            .where(
                Run.conversation_id == run.conversation_id,
                Run.id.in_([*path_ids(db, run.conversation_id, run.parent_run_id), run.id]),
                FileObject.user_id == run.user_id,
                FileObject.filename == filename,
                FileObject.deleted_at.is_(None),
                FileObject.purged_at.is_(None),
            )
            .order_by(FileObject.created_at.desc(), FileObject.version.desc())
            .limit(1)
        )
    artifact = Artifact(run_id=run.id, file_id=file_id, role=body.role)
    try:
        assign_file_version(db, file, parent)
        db.add(file)
        db.add(artifact)
        keys = trim_file_versions(db, file)
        append_event(
            db,
            run.id,
            "artifact.created",
            {
                "artifact_id": artifact.id,
                "file_id": file.id,
                "filename": file.filename,
                "size": file.size,
                "content_type": file.content_type,
                "role": artifact.role,
                "download_url": f"/v1/files/{file.id}/content",
            },
            commit=False,
        )
        _enqueue_thumbnail(db, file)
        db.flush()
        db.add(RunDelivery(run_id=run.id, delivery_id=delivery_id, result=_artifact_out(artifact, file)))
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(final_key)  # nothing references it; the staging copy is still there for the retry
        raise
    delete_storage_objects(keys)
    db.refresh(file)
    db.refresh(artifact)
    storage.delete(body.staging_key)

    return _artifact_out(artifact, file)


SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _skill_heads(db: Session, user_id: str, name: str) -> list[FileObject]:
    """The user's live personal skills that are, or were saved to become, `name`; newest first."""
    rows = db.scalars(
        select(FileObject)
        .where(
            FileObject.user_id == user_id,
            FileObject.purpose == "skill",
            FileObject.is_head == 1,
            FileObject.deleted_at.is_(None),
            FileObject.purged_at.is_(None),
        )
        .order_by(FileObject.created_at.desc())
    )
    return [f for f in rows if ((f.meta or {}).get("skill") or {}).get("name") == name or (f.meta or {}).get("skill_claim") == name]


@router.post("/runs/{run_id}/skills", status_code=201, dependencies=[Depends(require_scope("skills:save_for_run"))])
async def internal_save_skill(
    run_id: str,
    request: Request,
    name: str = Query(...),
    mode: Literal["create", "update"] = Query(...),
    filename: str = Query(...),
    expected_file_id: str | None = Query(default=None, max_length=40),
    x_run_attempt: str | None = Header(default=None),
    x_delivery_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """A personal skill written by the model, saved for the run's owner only.

    It enters the same path as an uploaded skill (PENDING, scan, review policy), so saving never makes it trusted.
    Refusals the model can act on are 422/404: on this internal API 409 means the attempt lost its run (fencing).
    create refuses a name in use; update adds a version to the user's own skill, keeping the old one restorable.
    Built-in skills and other users' skills are never candidates.
    """
    if not SKILL_NAME.match(name):
        raise HTTPException(422, "invalid skill name")
    if not filename.endswith((".zip", ".md")):
        raise HTTPException(422, "skill file must be .zip or .md")
    if not x_delivery_id or not x_delivery_id.startswith(run_id + ":") or len(x_delivery_id) > 160:
        raise HTTPException(422, "stable delivery identity required")
    limit = min(settings.max_upload_bytes, settings.max_sandbox_read_bytes)
    storage = get_storage()
    with tempfile.NamedTemporaryFile(prefix="ailab-skill-") as tmp:
        digest, size = hashlib.sha256(), 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"skill exceeds {limit} bytes")
            digest.update(chunk)
            tmp.write(chunk)
        tmp.flush()
        if size == 0:
            raise HTTPException(400, "empty skill")

        def record() -> dict:
            run = _run_or_404(db, run_id)
            with fenced_run(db, run, x_run_attempt) as run:
                prior = db.get(RunDelivery, (run.id, x_delivery_id))
                if prior:
                    return prior.result
                if run.status != RunStatus.RUNNING:
                    raise HTTPException(409, f"run is {run.status}")
                if name in builtin_skills():
                    raise HTTPException(422, f"{name} 是通用技能，不能被个人技能覆盖；请换个名字")
                heads = _skill_heads(db, run.user_id, name)
                if mode == "create" and heads:
                    raise HTTPException(422, f"已有同名个人技能 {name}；要改它请用 mode=update（会保留旧版本）")
                if mode == "update" and not heads:
                    raise HTTPException(404, f"没有名为 {name} 的个人技能；新建请用 mode=create")
                if mode == "update" and heads:
                    parent = heads[0]
                    if parent.id != expected_file_id:
                        raise HTTPException(422, "技能已更新或缺少版本，请先 load_skill 再传 expected_file_id")
                    if parent.status == FileStatus.PENDING:
                        raise HTTPException(422, "技能正在扫描，请完成后再更新")
                    if parent.filename.endswith(".zip") and not filename.endswith(".zip"):
                        raise HTTPException(422, "压缩包技能必须完整更新，不能用 Markdown 丢弃附带脚本")
                enforce_library_quota(db, run.user_id, size)
                file_id = new_id("fil")
                key = f"skills/{run.user_id}/{file_id}/{filename}"
                storage.put_file(key, tmp.name, "application/zip" if filename.endswith(".zip") else "text/markdown")
                try:
                    row = FileObject(
                        id=file_id,
                        user_id=run.user_id,
                        filename=filename,
                        content_type="application/zip" if filename.endswith(".zip") else "text/markdown",
                        size=size,
                        sha256=digest.hexdigest(),
                        storage_key=key,
                        status=FileStatus.PENDING,
                        purpose="skill",
                        meta={"processing": "queued", "skill_claim": name, "source": "agent", "run_id": run.id},
                    )
                    assign_file_version(db, row, heads[0] if heads else None)
                    db.add(row)
                    keys = trim_file_versions(db, row)
                    db.flush()
                    stage_job(db, "file", file_id=row.id)
                    result = {
                        "file_id": row.id,
                        "name": name,
                        "mode": mode,
                        "version": row.version,
                        "status": "pending",
                        "note": "已保存为个人技能，安全扫描与审核通过后可用",
                    }
                    append_event(db, run.id, "skill.saved", {k: result[k] for k in ("file_id", "name", "mode", "version")}, commit=False)
                    db.add(RunDelivery(run_id=run.id, delivery_id=x_delivery_id, result=result))
                    db.commit()
                except Exception:
                    db.rollback()
                    storage.delete(key)
                    raise
                delete_storage_objects(keys)
                return result

        return await asyncio.to_thread(record)


def _enqueue_thumbnail(db: Session, file: FileObject) -> None:
    if file.status == FileStatus.READY and not (file.meta or {}).get("thumb_key") and thumbnail_candidate(file.filename, file.content_type):
        stage_job(db, "thumbnail", file_id=file.id)


def _bound_file(
    db: Session, file_id: str, principal: ServicePrincipal, run_id: str | None, job: str, preview_job: str | None = None
) -> FileObject:
    file = db.get(FileObject, file_id)
    if file is None or file.deleted_at is not None:  # the trash is the owner's alone
        raise HTTPException(status_code=404, detail="file not found")
    if file.purged_at is not None:
        raise HTTPException(status_code=410, detail="version purged")
    if principal.service == "legacy":
        return file  # explicit dev/test compatibility, never enabled in secure
    if job == "preview" and principal.service == "broker":
        # A library render has no run: it is bound to the preview job the API recorded as pending for this file.
        meta = file.meta or {}
        pending = (meta.get("preview") or {}).get("status") == "pending"
        if "files:read_for_preview" not in principal.scopes or file.status != FileStatus.READY or not pending or not preview_job:
            raise HTTPException(status_code=403, detail="no pending preview job for this file")
        if meta.get("preview_job_id") != preview_job:
            raise HTTPException(status_code=403, detail="preview job superseded")
        return file
    if principal.service == "worker":
        allowed = file.status == FileStatus.PENDING if job == "processing" else file.status == FileStatus.READY
        if not allowed:
            raise HTTPException(status_code=403, detail="file is not eligible for this processing job")
        return file
    if not run_id:
        raise HTTPException(status_code=403, detail="run_id required")
    run = _run_or_404(db, run_id)
    if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
        raise HTTPException(status_code=403, detail="run is not active")
    context = RunContext(run.id, run.user_id, run.conversation_id)
    template = (run.options or {}).get("template") or {}
    if job == "template":
        if (
            principal.service == "broker"
            and template.get("file_id") == file.id
            and file.status == FileStatus.READY
            and (run.options or {}).get("mode", "work") == "work"
        ):
            return file
        raise HTTPException(404, "template not bound to this run")
    if file.purpose == "template":
        # Historical brand snapshots may still reference the promoted brand slot.
        if not (job == "brand" and file.id in (((run.runtime_meta or {}).get("brand") or {}).get("assets") or {}).values()):
            raise HTTPException(404, "template files require the template injection path")
    if file.purpose in {"brand", "template"}:
        snapshot = (run.runtime_meta or {}).get("brand") or {}
        if (
            principal.service == "broker"
            and job == "brand"
            and file.status == FileStatus.READY
            and file.id in (snapshot.get("assets") or {}).values()
            and (run.options or {}).get("mode", "work") == "work"
        ):
            return file
        raise HTTPException(404, "brand assets require the brand injection path")
    if job == "brand":
        raise HTTPException(404, "not a brand asset")
    if file.user_id != context.user_id or file.status != FileStatus.READY:
        raise HTTPException(status_code=404, detail="file not found for run")
    attached = file.id in (run.input_file_ids or [])
    if principal.service == "broker" and file.purpose == "library" and file.id in ((run.runtime_meta or {}).get("library_file_ids") or []):
        attached = True
    if not attached and run.conversation_id:
        ancestors = path_ids(db, run.conversation_id, run.parent_run_id)
        elements = (
            func.json_array_elements_text(Run.input_file_ids).table_valued("value")
            if db.bind.dialect.name == "postgresql"
            else func.json_each(Run.input_file_ids).table_valued("value")
        )
        attached = bool(
            db.scalar(
                select(
                    exists().where(
                        Run.conversation_id == run.conversation_id,
                        Run.id.in_(ancestors),
                        Run.user_id == run.user_id,
                        exists(select(1).select_from(elements).where(elements.c.value == file.id)).correlate(Run),
                    )
                )
            )
        )
        if not attached:
            attached = (
                db.scalar(
                    select(Artifact.id)
                    .join(Run, Run.id == Artifact.run_id)
                    .where(
                        Artifact.file_id == file.id,
                        Run.conversation_id == run.conversation_id,
                        Run.id.in_(ancestors),
                        Run.user_id == run.user_id,
                    )
                    .limit(1)
                )
                is not None
            )
    if not attached:
        attached = db.scalar(select(Artifact.id).where(Artifact.run_id == run.id, Artifact.file_id == file.id).limit(1)) is not None
    # Enabled personal skills are a second explicit, API-controlled attachment source.
    if not attached and principal.service == "agent":
        owner = db.get(User, run.user_id)
        attached = bool(owner and any(skill["file_id"] == file.id for skill in usable_personal(db, owner).values()))
    if not attached:
        raise HTTPException(status_code=404, detail="file not attached to run")
    return file


FILE_READ = require_scope("files:read_for_run", "files:read_pending", "files:read_for_preview")
FileJob = Literal["processing", "thumbnail", "preview", "brand", "template"]


@router.get("/files/{file_id}", response_model=FileOut)
def internal_get_file(
    file_id: str,
    run_id: str | None = None,
    job: FileJob = "processing",
    preview_job: str | None = None,
    principal: ServicePrincipal = Depends(FILE_READ),
    db: Session = Depends(get_db),
) -> dict:
    return file_dict(_bound_file(db, file_id, principal, run_id, job, preview_job))


@router.get("/files/{file_id}/content")
def internal_get_file_content(
    file_id: str,
    run_id: str | None = None,
    job: FileJob = "processing",
    preview_job: str | None = None,
    principal: ServicePrincipal = Depends(FILE_READ),
    db: Session = Depends(get_db),
) -> Response:
    file = _bound_file(db, file_id, principal, run_id, job, preview_job)
    key, size = file.storage_key, file.size
    media = file.content_type
    if job == "brand" and (file.meta or {}).get("brand_kind") == "logo":
        key = f"derived/{file.id}/logo.png"
        size = (file.meta or {}).get("brand", {}).get("png_bytes", 0)
        media = "image/png"
    db.close()
    from .routes_files import _ClosingStreamingResponse

    return _ClosingStreamingResponse(
        get_storage().stream(key),
        media_type=media,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename)}",
            "Content-Length": str(size),
        },
    )


@router.patch("/files/{file_id}", response_model=FileOut)
def internal_patch_file(
    file_id: str,
    body: FilePatch,
    job: FileJob = "processing",
    principal: ServicePrincipal = Depends(require_scope("files:patch_processing", "files:preview_write")),
    db: Session = Depends(get_db),
) -> dict:
    file = _bound_file(db, file_id, principal, None, job)
    from .brand import brand_lock

    lock = brand_lock(db) if file.purpose == "brand" else user_critical_section(db, file.user_id)
    with lock:
        db.refresh(file)
        return _apply_file_patch(db, file, body, principal, job)


def _apply_file_patch(db: Session, file: FileObject, body: FilePatch, principal: ServicePrincipal, job: str) -> dict:
    if principal.service != "legacy" and job != "processing":
        allowed = {"thumb_key", "display_key"} if job == "thumbnail" else set()  # previews complete via /preview
        if body.status is not None or not set(body.meta or {}) <= allowed:
            raise HTTPException(status_code=403, detail="derived jobs cannot change scan or processing state")
        for key, value in (body.meta or {}).items():
            if key.endswith("_key") and (
                not isinstance(value, str) or not value.startswith(f"derived/{file.id}/") or ".." in value.split("/")
            ):
                raise HTTPException(status_code=403, detail="derived key must belong to this file")
    if body.meta is not None:
        merged = dict(file.meta or {})
        merged.update(body.meta)
        claim = merged.get("skill_claim")
        if claim and merged.get("skill") and merged["skill"].get("name") != claim:
            # Saved as one name, its SKILL.md says another: it must not quietly replace a different skill.
            merged.pop("skill")
            merged["skill_error"] = f"SKILL.md 里的 name 与保存时的名称 {claim} 不一致"
        file.meta = merged
        if merged.get("skill") or merged.get("skill_error"):
            head = db.scalar(select(FileObject).where(FileObject.lineage_id == file.lineage_id, FileObject.is_head == 1)) or file
            head.purpose, head.project_id = "skill", None
            reserve_filename(db, head)
            db.execute(
                update(FileObject)
                .where(FileObject.lineage_id == file.lineage_id)
                .values(purpose="skill", project_id=None, filename=head.filename, name_key=head.name_key)
            )
    if body.status is not None:
        if body.status not in {FileStatus.PENDING, FileStatus.READY, FileStatus.REJECTED}:
            raise HTTPException(status_code=400, detail=f"bad file status: {body.status}")
        file.status = body.status
    if file.purpose == "brand":
        from .brand import activate_asset

        activate_asset(db, file)
    if file.status == FileStatus.READY and (file.meta or {}).get("ocr") == "pending" and "ocr_requested_at" not in (file.meta or {}):
        request_ocr(db, file)  # same transaction: the job exists exactly when the file becomes ready with OCR pending
    if file.purpose == "template" and file.status == FileStatus.READY and (file.meta or {}).get("template"):
        from .preview_requests import request_preview

        request_preview(db, file, retry_stale=False)
    from .routes_library_summary import request_summary

    request_summary(db, file)
    db.commit()
    db.refresh(file)
    return file_dict(file)


@router.post("/files/{file_id}/preview", dependencies=[Depends(require_scope("files:preview_write"))])
def complete_file_preview(file_id: str, body: PreviewCompletion, db: Session = Depends(get_db)) -> dict:
    """Worker completion is bound to the current request, so stale jobs cannot overwrite a newer one."""
    file = db.get(FileObject, file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="file not found")
    with user_critical_section(db, file.user_id):
        db.refresh(file)
        meta = dict(file.meta or {})
        if file.status != FileStatus.READY or meta.get("preview_job_id") != body.job_id:
            raise HTTPException(status_code=409, detail="preview job superseded")
        preview = body.preview
        if preview.status not in {"ready", "failed"}:
            raise HTTPException(status_code=400, detail="invalid completion")
        assets = {name: asset.model_dump() for name, asset in body.assets.items()}
        for name, asset in assets.items():
            if "/" in name or "\\" in name or name in {".", ".."} or asset.get("key") != f"derived/{file_id}/preview/{name}":
                raise HTTPException(status_code=400, detail="invalid asset")
        thumbnail = assets.get("template-thumbnail.jpg")
        if thumbnail and (file.purpose != "template" or preview.status != "ready" or preview.kind != "pages"):
            raise HTTPException(status_code=400, detail="invalid template thumbnail")
        expected = (
            set(preview.pages or [])
            if preview.kind == "pages"
            else {preview.data}
            if preview.kind == "sheets"
            else {preview.text}
            if preview.kind == "text"
            else set()
        )
        if thumbnail:
            expected.add("template-thumbnail.jpg")
        if preview.status == "ready" and preview.kind in {"pages", "sheets", "text"} and (not expected or set(assets) != expected):
            raise HTTPException(status_code=400, detail="manifest assets do not match")
        if (meta.get("preview") or {}).get("status") != "pending":
            return {"ok": True}
        meta["preview"] = preview.model_dump(exclude_unset=True)
        for asset in assets.values():
            if asset["content_type"] != {"pages": "image/jpeg", "sheets": "application/json", "text": "text/plain; charset=utf-8"}.get(
                preview.kind
            ):
                raise HTTPException(status_code=400, detail="invalid asset content type")
        meta["preview_assets"] = assets
        if thumbnail:
            meta["template_preview_thumbnail"] = thumbnail
        if preview.image == "display.webp":
            meta["display_key"] = f"derived/{file_id}/display.webp"
            meta["thumb_key"] = f"derived/{file_id}/thumb.webp"
        file.meta = meta
        db.commit()
        return {"ok": True}


@router.put("/runs/{run_id}/conversation-title", dependencies=[Depends(require_scope("runs:status"))])
def set_conversation_title(
    run_id: str, body: ConversationTitle, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)
):
    run = _run_or_404(db, run_id)
    with fenced_run(db, run, x_run_attempt) as run:
        if run.status == RunStatus.CANCELED:
            return {"ok": False}
        title = normalize_title(body.title)
        changed = (
            db.execute(
                update(Conversation)
                .where(
                    Conversation.id == run.conversation_id,
                    Conversation.user_id == run.user_id,
                    Conversation.deleted_at.is_(None),
                    Conversation.title == "",
                )
                .values(title=title)
            ).rowcount
            if title
            else 0
        )
        if changed:
            append_event(db, run.id, "conversation.titled", {"conversation_id": run.conversation_id, "title": title}, commit=False)
        db.commit()
        return {"ok": bool(changed)}
