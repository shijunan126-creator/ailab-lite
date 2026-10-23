"""Internal service-to-service API.

Every route requires ``X-Service-Token``. Agent, Broker and Worker use these
endpoints instead of touching the database or object storage ledger directly.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..contracts.usage import ReservationOut, ReserveUsageIn, SettleUsageIn, UsageErrorCode, UsageOut
from ..deps import get_db
from ..events import append_event
from ..library import thumbnail_candidate
from ..locks import user_critical_section
from ..models import Artifact, Conversation, FileObject, FileStatus, Project, Run, RunStatus, User, new_id
from ..ports.service_auth import RunContext
from ..queue import get_queue
from ..quota import enforce_library_quota, reserve_usage, run_usage_totals, settle_usage
from ..run_state import InvalidTransition, transition_run
from ..schemas import (
    ArtifactIn,
    CommitOut,
    EventIn,
    EventOut,
    FileOut,
    FilePatch,
    InternalRunDetail,
    RunDetail,
    RunStateOut,
    RunStatusIn,
)
from ..service_auth import ServicePrincipal, require_scope
from ..storage import get_storage
from .routes_skills import usable_personal
from .serializers import file_dict, run_dict

router = APIRouter(prefix="/internal", tags=["internal"])
log = logging.getLogger("ailab.api.internal")


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
        if f is None:
            continue
        meta = f.meta or {}
        text = ""
        if meta.get("text_key"):
            try:
                text = storage.get_bytes(meta["text_key"]).decode("utf-8", "replace")
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


def workspace_alive(run: Run, now: datetime) -> bool:
    """Whether this turn's workspace is still inside the retention window.

    New snapshots carry workspace_saved_at; older rows fall back to finished_at
    so deploying the TTL does not resurrect or lose their existing snapshots.
    """
    result = run.result or {}
    if not result.get("workspace_saved"):
        return False
    saved_at = result.get("workspace_saved_at")
    when: datetime | None = None
    if isinstance(saved_at, str):
        try:
            when = datetime.fromisoformat(saved_at)
        except ValueError:
            when = None
    when = when or run.finished_at or run.created_at
    if when is None:
        return False
    if when.tzinfo is None:  # SQLite drops tzinfo
        when = when.replace(tzinfo=UTC)
    if settings.workspace_ttl_days <= 0:  # 0 = no expiry (not "expire at once")
        return True
    return when + timedelta(days=settings.workspace_ttl_days) > now


def _earlier_turns(db: Session, run: Run) -> list[Run]:
    if not run.conversation_id:
        return []
    return list(
        db.scalars(
            select(Run)
            .where(Run.conversation_id == run.conversation_id, Run.created_at <= run.created_at, Run.id != run.id)
            .order_by(Run.created_at, Run.id)
        )
    )


@router.get("/runs/{run_id}", response_model=InternalRunDetail, dependencies=[Depends(require_scope("runs:read"))])
def internal_get_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    """The run, and for a conversation turn: the earlier turns (for the model) and the workspace to restore (for the Broker)."""
    run = _run_or_404(db, run_id)
    out = run_dict(db, run, detail=True)
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
        history.append({"prompt": turn.prompt, "reply": reply[:HISTORY_CHARS], "status": turn.status, "files": files})
    out["history"] = history
    out["input_files"] = _attachment_texts(db, run)
    owner = db.get(User, run.user_id)
    out["disabled_skills"] = list(((owner.settings if owner else None) or {}).get("disabled_skills") or [])
    out["personal_skills"] = (
        [{k: s[k] for k in ("name", "title", "description", "file_id", "filename", "kind")} for s in usable_personal(db, owner).values()]
        if owner
        else []
    )
    now = datetime.now(UTC)
    newest = next((t for t in reversed(earlier) if (t.result or {}).get("workspace_saved")), None)
    alive = newest is not None and workspace_alive(newest, now)
    out["workspace_restore_key"] = workspace_key(newest.id) if alive else None
    # the last turn left files, but they are past the retention window: tell the model instead of letting it hunt
    out["workspace_expired"] = newest is not None and not alive
    return out


@router.get("/runs/{run_id}/state", response_model=RunStateOut, dependencies=[Depends(require_scope("runs:read"))])
def internal_run_state(run_id: str, db: Session = Depends(get_db)) -> dict:
    """The cheap status poll the Agent uses while a command/model call is running."""
    run = _run_or_404(db, run_id)
    return {"status": run.status, "finished_at": run.finished_at}


@router.post("/runs/{run_id}/status", response_model=RunDetail, dependencies=[Depends(require_scope("runs:status"))])
def internal_set_status(run_id: str, body: RunStatusIn, db: Session = Depends(get_db)) -> dict:
    run = _run_or_404(db, run_id)
    if body.status not in {RunStatus.RUNNING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED}:
        raise HTTPException(status_code=400, detail=f"bad status: {body.status}")
    try:
        with user_critical_section(db, run.user_id):
            db.refresh(run)
            result = body.result
            if result is not None:
                result = {**result, "usage_totals": run_usage_totals(db, run_id)}
            run, changed = transition_run(db, run_id, body.status, summary=body.summary, error=body.error, result=result)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=f"invalid transition: {exc}") from exc
    if changed and (run.result or {}).get("workspace_saved"):
        _drop_older_workspaces(db, run)
    return run_dict(db, run, detail=True)


def _drop_older_workspaces(db: Session, run: Run) -> None:
    """Only the newest workspace of a conversation is ever restored; delete the ones before it (best effort)."""
    storage = get_storage()
    for turn in _earlier_turns(db, run):
        result = dict(turn.result or {})
        if not result.get("workspace_saved"):
            continue
        try:
            storage.delete(workspace_key(turn.id))
        except Exception:
            log.warning("could not delete workspace of %s", turn.id, exc_info=True)
            continue
        with user_critical_section(db, run.user_id):
            # Storage I/O may overlap a late usage settlement; merge the current JSON.
            db.refresh(turn)
            turn.result = {**(turn.result or {}), "workspace_saved": False}
            db.commit()


@router.post("/runs/{run_id}/events", response_model=EventOut, dependencies=[Depends(require_scope("runs:events"))])
def internal_append_event(run_id: str, body: EventIn, db: Session = Depends(get_db)) -> dict:
    _run_or_404(db, run_id)
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
    row = reserve_usage(db, body)
    return {
        "call_id": row.call_id,
        "run_id": row.run_id,
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
    row = settle_usage(db, body)
    totals = run_usage_totals(db, row.run_id)
    return {
        "call_id": row.call_id,
        "run_id": row.run_id,
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
def internal_commit_artifact(body: ArtifactIn, db: Session = Depends(get_db)) -> dict:
    """Second half of the two-phase delivery. Safe to retry.

    Same (run, filename, sha256) returns the artifact that already exists instead
    of creating a duplicate, and the staging object is only removed after the
    database row is committed, so a failure in between can be retried.
    """
    run = _run_or_404(db, body.run_id)
    with user_critical_section(db, run.user_id):
        db.refresh(run)
        return _commit_artifact(body, db, run)


def _commit_artifact(body: ArtifactIn, db: Session, run: Run) -> dict:
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
        _enqueue_thumbnail(existing[1])
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
    # Hash the staged object in chunks: artifact limits allow 100 MB and the
    # API should not hold a second full copy just to verify it.
    hasher = hashlib.sha256()
    for chunk in storage.stream(body.staging_key):
        hasher.update(chunk)
    digest = hasher.hexdigest()
    if digest != body.sha256:
        raise reject(400, "sha256 mismatch")

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
    artifact = Artifact(run_id=run.id, file_id=file_id, role=body.role)
    try:
        db.add(file)
        db.add(artifact)
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(final_key)  # nothing references it; the staging copy is still there for the retry
        raise
    db.refresh(file)
    db.refresh(artifact)
    storage.delete(body.staging_key)

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
    )
    _enqueue_thumbnail(file)
    return _artifact_out(artifact, file)


def _enqueue_thumbnail(file: FileObject) -> None:
    if file.status == FileStatus.READY and not (file.meta or {}).get("thumb_key") and thumbnail_candidate(file.filename, file.content_type):
        try:
            get_queue().enqueue(settings.file_queue, {"file_id": file.id, "thumbnail_only": True})
        except Exception:
            log.warning("thumbnail queue unavailable for %s", file.id, exc_info=True)


def _bound_file(db: Session, file_id: str, principal: ServicePrincipal, run_id: str | None, job: str) -> FileObject:
    file = db.get(FileObject, file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="file not found")
    if principal.service == "legacy":
        return file  # explicit dev/test compatibility, never enabled in secure
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
    if file.user_id != context.user_id or file.status != FileStatus.READY:
        raise HTTPException(status_code=404, detail="file not found for run")
    attached = file.id in (run.input_file_ids or [])
    if not attached and run.conversation_id:
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
                    .where(Artifact.file_id == file.id, Run.conversation_id == run.conversation_id, Run.user_id == run.user_id)
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


FILE_READ = require_scope("files:read_for_run", "files:read_pending")
FileJob = Literal["processing", "thumbnail", "preview"]


@router.get("/files/{file_id}", response_model=FileOut)
def internal_get_file(
    file_id: str,
    run_id: str | None = None,
    job: FileJob = "processing",
    principal: ServicePrincipal = Depends(FILE_READ),
    db: Session = Depends(get_db),
) -> dict:
    return file_dict(_bound_file(db, file_id, principal, run_id, job))


@router.get("/files/{file_id}/content")
def internal_get_file_content(
    file_id: str,
    run_id: str | None = None,
    job: FileJob = "processing",
    principal: ServicePrincipal = Depends(FILE_READ),
    db: Session = Depends(get_db),
) -> Response:
    file = _bound_file(db, file_id, principal, run_id, job)
    db.close()
    from .routes_files import _ClosingStreamingResponse

    return _ClosingStreamingResponse(
        get_storage().stream(file.storage_key),
        media_type=file.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename)}",
            "Content-Length": str(file.size),
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
    if principal.service != "legacy" and job != "processing":
        allowed = {"thumb_key"} if job == "thumbnail" else {"preview_key", "preview_content_type", "preview_error"}
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
        file.meta = merged
    if body.status is not None:
        if body.status not in {FileStatus.PENDING, FileStatus.READY, FileStatus.REJECTED}:
            raise HTTPException(status_code=400, detail=f"bad file status: {body.status}")
        file.status = body.status
    db.commit()
    db.refresh(file)
    return file_dict(file)
