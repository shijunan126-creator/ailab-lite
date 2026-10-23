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
from ..deps import get_db
from ..events import append_event
from ..library import assign_file_version, delete_storage_objects, reserve_filename, thumbnail_candidate, trim_file_versions
from ..locks import user_critical_section
from ..models import Artifact, Conversation, FileObject, FileStatus, Project, Run, RunStatus, UsageReservation, User, new_id
from ..ports.service_auth import RunContext
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
    RunTransitionIn,
)
from ..service_auth import ServicePrincipal, require_scope
from ..storage import get_storage
from .jobs import stage_job
from .routes_ocr import request_ocr
from .routes_skills import skill_input_for, usable_personal
from .runtime_fence import fence_attempt
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
    conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    out["needs_conversation_title"] = bool(conversation and conversation.deleted_at is None and not conversation.title)
    out["history"] = history
    out["input_files"] = _attachment_texts(db, run)
    owner = db.get(User, run.user_id)
    out.update(skill_input_for(db, owner))
    now = datetime.now(UTC)
    newest = next((t for t in reversed(earlier) if (t.result or {}).get("workspace_saved")), None)
    alive = newest is not None and workspace_alive(newest, now)
    own = (run.runtime_meta or {}).get("workspace")
    out["workspace_restore_key"] = (
        own["key"] if own else ((newest.result or {}).get("workspace_key") or workspace_key(newest.id)) if alive else None
    )
    # the last turn left files, but they are past the retention window: tell the model instead of letting it hunt
    out["workspace_expired"] = newest is not None and not alive
    return out


@router.get("/runs/{run_id}/state", response_model=RunStateOut, dependencies=[Depends(require_scope("runs:read"))])
def internal_run_state(run_id: str, db: Session = Depends(get_db)) -> dict:
    """The cheap status poll the Agent uses while a command/model call is running."""
    run = _run_or_404(db, run_id)
    return {"status": run.status, "finished_at": run.finished_at, "job_id": (run.runtime_meta or {}).get("job_id", run.id)}


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
    with user_critical_section(db, run.user_id):
        db.refresh(run)
        fence_attempt(db, run_id, x_run_attempt or body.attempt_id)
        try:
            result = body.result
            if result is not None:
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
            storage.delete((turn.result or {}).get("workspace_key") or workspace_key(turn.id))
        except Exception:
            log.warning("could not delete workspace of %s", turn.id, exc_info=True)
            continue
        with user_critical_section(db, run.user_id):
            # Storage I/O may overlap a late usage settlement; merge the current JSON.
            db.refresh(turn)
            turn.result = {**(turn.result or {}), "workspace_saved": False}
            db.commit()


@router.post("/runs/{run_id}/events", response_model=EventOut, dependencies=[Depends(require_scope("runs:events"))])
def internal_append_event(
    run_id: str, body: EventIn, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)
) -> dict:
    run = _run_or_404(db, run_id)
    with user_critical_section(db, run.user_id):
        fence_attempt(db, run_id, x_run_attempt)
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
    with user_critical_section(db, run.user_id):
        db.refresh(run)
        fence_attempt(db, run.id, x_run_attempt)
        from ..models import RunDelivery

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
    from ..models import RunDelivery

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
    parent = None
    if run.conversation_id:
        parent = db.scalar(
            select(FileObject)
            .join(Artifact, Artifact.file_id == FileObject.id)
            .join(Run, Run.id == Artifact.run_id)
            .where(
                Run.conversation_id == run.conversation_id,
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
            from ..models import RunDelivery

            run = _run_or_404(db, run_id)
            with user_critical_section(db, run.user_id):
                db.refresh(run)
                fence_attempt(db, run.id, x_run_attempt)
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
        from .jobs import stage_job

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


FILE_READ = require_scope("files:read_for_run", "files:read_pending", "files:read_for_preview")
FileJob = Literal["processing", "thumbnail", "preview"]


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
    with user_critical_section(db, file.user_id):
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
    if file.status == FileStatus.READY and (file.meta or {}).get("ocr") == "pending" and "ocr_requested_at" not in (file.meta or {}):
        request_ocr(db, file)  # same transaction: the job exists exactly when the file becomes ready with OCR pending
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
        expected = (
            set(preview.pages or [])
            if preview.kind == "pages"
            else {preview.data}
            if preview.kind == "sheets"
            else {preview.text}
            if preview.kind == "text"
            else set()
        )
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
        if preview.image == "display.webp":
            meta["display_key"] = f"derived/{file_id}/display.webp"
            meta["thumb_key"] = f"derived/{file_id}/thumb.webp"
        file.meta = meta
        db.commit()
        return {"ok": True}
