"""API-owned durable job state: stage in business transaction, CAS claim, ACK last."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from .. import obs
from ..config import settings
from ..contracts.jobs import JobEnvelope
from ..deps import get_db
from ..locks import user_critical_section
from ..models import FileObject, FileStatus, OutboxMessage, Run, RunAttempt, RunStatus, SkillDraft
from ..obs import NO_REQUEST, request_id_var
from ..ports.service_auth import ServicePrincipal
from ..queue import job_topic
from ..service_auth import require_scope

router = APIRouter(prefix="/internal/jobs", tags=["internal-jobs"])


def stage_job(
    db: Session, kind: str, *, run_id=None, file_id=None, draft_id=None, mode="work", job_id=None, priority=False
) -> OutboxMessage:
    envelope = JobEnvelope(
        job_id=job_id or run_id or (f"thumb_{file_id}" if kind == "thumbnail" else uuid4().hex),
        kind=kind,
        run_id=run_id,
        file_id=file_id,
        draft_id=draft_id,
        mode=mode,
        priority=priority,
        deadline=datetime.now(UTC) + timedelta(seconds=settings.job_deadline_seconds),
        enqueued_at=datetime.now(UTC),
        traceparent=getattr(obs, "traceparent_var").get() if hasattr(obs, "traceparent_var") else None,
        request_id=None if request_id_var.get() == NO_REQUEST else request_id_var.get(),
    )
    topic = job_topic(kind, mode, priority)
    existing = db.get(OutboxMessage, envelope.job_id)
    if existing:
        return existing
    row = OutboxMessage(id=envelope.job_id, topic=topic, payload=envelope.model_dump(mode="json"))
    db.add(row)
    return row


class ClaimIn(BaseModel):
    consumer: str = Field(min_length=1, max_length=120)


class FinishIn(BaseModel):
    token: str = Field(min_length=1, max_length=40)
    retry_after_seconds: int = Field(default=0, ge=0, le=60)


def _job(db, job_id, principal):
    job = db.get(OutboxMessage, job_id)
    if job is not None:
        scope = "jobs:run" if job.payload.get("kind") == "run" else "jobs:file"
        if scope not in principal.scopes:
            raise HTTPException(403, "job scope required")
    return job


@router.post("/{job_id}/claim")
def claim_job(
    job_id: str, body: ClaimIn, principal: ServicePrincipal = Depends(require_scope("jobs:run", "jobs:file")), db: Session = Depends(get_db)
):
    job = _job(db, job_id, principal)
    if job is None:
        return {"state": "done"}
    envelope = JobEnvelope.model_validate(job.payload)
    resource = (
        db.get(Run, envelope.run_id)
        if envelope.kind == "run"
        else db.get(SkillDraft, envelope.draft_id)
        if envelope.kind == "skill_unpack"
        else db.get(FileObject, envelope.file_id)
    )
    guard = user_critical_section(db, resource.user_id) if resource is not None else nullcontext()
    with guard:
        # A concurrent file patch may have committed while this request waited for the user lock.
        if resource is not None:
            db.refresh(resource)
        return _claim_locked(db, job, body)


def _claim_locked(db, job, body):
    job_id = job.id
    now = datetime.now(UTC)
    token = uuid4().hex
    ttl = settings.job_lease_seconds if job.payload["kind"] == "run" else 660
    won = db.execute(
        update(OutboxMessage)
        .execution_options(synchronize_session=False)
        .where(
            OutboxMessage.id == job_id,
            or_(OutboxMessage.claimed_until.is_(None), OutboxMessage.claimed_until <= now),
        )
        .values(
            claim_token=token,
            claimed_until=now + timedelta(seconds=ttl),
            next_attempt_at=now + timedelta(seconds=ttl),
            deliveries=OutboxMessage.deliveries + 1,
        )
    )
    if not won.rowcount:
        db.rollback()
        return {"state": "busy"}
    db.refresh(job)
    envelope = JobEnvelope.model_validate(job.payload)
    if envelope.kind == "run":
        run = db.get(Run, envelope.run_id)
        done = (
            run is None
            or run.status in RunStatus.TERMINAL | {RunStatus.WAITING_USER, RunStatus.INTERRUPTED}
            or (run.runtime_meta or {}).get("job_id", job_id) != job_id
        )
        if not done:
            previous = db.scalars(select(RunAttempt).where(RunAttempt.run_id == run.id, RunAttempt.ended_at.is_(None))).all()
            for attempt in previous:
                attempt.ended_at, attempt.outcome = now, "abandoned"
            # Deliveries count within one job, so a continuation after an answer starts with a fresh budget.
            if job.deliveries > settings.run_max_attempts:
                from ..run_state import transition_run

                transition_run(
                    db, run.id, RunStatus.FAILED, error=f"agent crashed or hung {settings.run_max_attempts} times; giving up", commit=False
                )
                done = True
        if not done:
            number = (
                db.scalar(select(RunAttempt.number).where(RunAttempt.run_id == run.id).order_by(RunAttempt.number.desc()).limit(1)) or 0
            ) + 1
            db.add(RunAttempt(id=token, run_id=run.id, number=number, agent_instance=body.consumer))
    elif envelope.kind == "skill_unpack":
        draft = db.get(SkillDraft, envelope.draft_id)
        file = db.get(FileObject, draft.base_file_id) if draft else None
        done = (
            not draft
            or draft.status != "preparing"
            or draft.claim_token != job_id
            or not file
            or file.user_id != draft.user_id
            or file.deleted_at is not None
            or file.purged_at is not None
            or not file.is_head
        )
    else:
        file = db.get(FileObject, envelope.file_id)
        done = file is None or file.deleted_at is not None or file.purged_at is not None
        if not done:
            meta = file.meta or {}
            done = (
                (envelope.kind == "file" and (file.status != FileStatus.PENDING or meta.get("scan_exhausted")))
                or (envelope.kind == "thumbnail" and (file.status != FileStatus.READY or bool(meta.get("thumb_key"))))
                or (
                    envelope.kind == "preview"
                    and (meta.get("preview_job_id") != job_id or (meta.get("preview") or {}).get("status") != "pending")
                )
                or (envelope.kind == "ocr" and meta.get("ocr") not in {"pending", "running"})
                or (
                    envelope.kind == "summary"
                    and (
                        (meta.get("summary") or {}).get("job_id") != job_id
                        or (meta.get("summary") or {}).get("status") in {"done", "skipped"}
                        or ((meta.get("summary") or {}).get("status") == "failed" and (meta.get("summary") or {}).get("attempt", 0) >= 3)
                    )
                )
            )
    expired = envelope.deadline <= now
    render_waiting = (
        envelope.kind == "preview" or (envelope.kind == "file" and (file.meta or {}).get("processing") == "render_pending")
        if not done and envelope.kind in {"preview", "file"}
        else False
    )
    if not done and render_waiting:
        if envelope.kind == "file":
            # Capacity waits do not consume the scanner failure budget.
            job.deliveries = 1
        if expired:
            job.payload = {**job.payload, "deadline": (now + timedelta(seconds=settings.job_deadline_seconds)).isoformat()}
            expired = False
    if expired and envelope.kind == "run" and not done:
        from ..run_state import transition_run

        transition_run(db, envelope.run_id, RunStatus.FAILED, error="job deadline exceeded", commit=False)
    if expired or done:
        if envelope.kind == "skill_unpack" and draft and draft.status == "preparing" and draft.claim_token == job_id:
            draft.status, draft.stage, draft.error = "failed", None, "技能文件已失效或解包超时，请重试"
            draft.claim_token = None
        if expired and envelope.kind == "run" and not done:
            db.flush()
            attempt = db.get(RunAttempt, token)
            if attempt:
                attempt.ended_at, attempt.outcome = now, "deadline"
        if expired and envelope.kind == "file" and not done:
            file.meta = {**(file.meta or {}), "processing": "job_expired", "scan_exhausted": True}
        elif expired and envelope.kind == "preview" and not done:
            file.meta = {**(file.meta or {}), "preview": {"status": "failed", "reason": "job_expired"}}
        elif expired and envelope.kind == "ocr" and not done:
            file.meta = {**(file.meta or {}), "ocr": "failed", "ocr_reason": "job_expired"}
        elif expired and envelope.kind == "summary" and not done:
            file.meta = {
                **(file.meta or {}),
                "summary": {**(file.meta or {}).get("summary", {}), "status": "failed", "reason": "job_expired"},
            }
        db.delete(job)
        db.commit()
        return {"state": "done"}
    db.commit()
    # attempt counts deliveries of this job, so a continuation after an answer starts at 1 and only a re-claim after a death is > 1
    return {"state": "claimed", "token": token, "attempt": job.deliveries, "attempt_id": token if envelope.kind == "run" else None}


@router.post("/{job_id}/renew")
def renew_job(
    job_id: str,
    body: FinishIn,
    principal: ServicePrincipal = Depends(require_scope("jobs:run", "jobs:file")),
    db: Session = Depends(get_db),
):
    job = _job(db, job_id, principal)
    if job is None:
        raise HTTPException(409, "stale_claim")
    now = datetime.now(UTC)
    ttl = settings.job_lease_seconds if job.payload["kind"] == "run" else 660
    won = db.execute(
        update(OutboxMessage)
        .where(OutboxMessage.id == job_id, OutboxMessage.claim_token == body.token, OutboxMessage.claimed_until > now)
        .values(claimed_until=now + timedelta(seconds=ttl), next_attempt_at=now + timedelta(seconds=ttl))
        .execution_options(synchronize_session=False)
    ).rowcount
    if not won:
        raise HTTPException(409, "stale_claim")
    db.commit()
    return {"ok": True}


@router.post("/{job_id}/ack")
def ack_job(
    job_id: str,
    body: FinishIn,
    principal: ServicePrincipal = Depends(require_scope("jobs:run", "jobs:file")),
    db: Session = Depends(get_db),
):
    job = _job(db, job_id, principal)
    if job is None:
        return {"ok": True}
    if job.claim_token != body.token:
        raise HTTPException(409, "stale_claim")
    deleted = db.execute(
        delete(OutboxMessage)
        .where(
            OutboxMessage.id == job_id,
            OutboxMessage.claim_token == body.token,
            OutboxMessage.claimed_until > datetime.now(UTC),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if not deleted:
        raise HTTPException(409, "stale_claim")
    attempt = db.get(RunAttempt, body.token)
    if attempt:
        attempt.ended_at, attempt.outcome = datetime.now(UTC), "acked"
    db.commit()
    return {"ok": True}


@router.post("/{job_id}/release")
def release_job(
    job_id: str,
    body: FinishIn,
    principal: ServicePrincipal = Depends(require_scope("jobs:run", "jobs:file")),
    db: Session = Depends(get_db),
):
    job = _job(db, job_id, principal)
    if job is None:
        return {"ok": True}
    now = datetime.now(UTC)
    changed = db.execute(
        update(OutboxMessage)
        .execution_options(synchronize_session=False)
        .where(OutboxMessage.id == job_id, OutboxMessage.claim_token == body.token)
        .values(
            claim_token=None,
            claimed_until=None,
            next_attempt_at=now + timedelta(seconds=body.retry_after_seconds),
        )
    ).rowcount
    if not changed:
        raise HTTPException(409, "stale_claim")
    attempt = db.get(RunAttempt, body.token)
    if attempt:
        attempt.ended_at, attempt.outcome = now, "released"
    db.commit()
    return {"ok": True}


class ThumbnailBatchIn(BaseModel):
    cursor: str = Field(default="", max_length=40)
    limit: int = Field(default=200, ge=1, le=1000)


@router.post("/thumbnails/backfill", dependencies=[Depends(require_scope("jobs:file"))])
def thumbnail_batch(body: ThumbnailBatchIn, db: Session = Depends(get_db)):
    from ..library import thumbnail_candidate

    # Primary-key keyset bounds both the scan and the transaction, even for non-images.
    files = db.scalars(select(FileObject).where(FileObject.id > body.cursor).order_by(FileObject.id).limit(body.limit)).all()

    def missing(file):
        return (
            file.status == FileStatus.READY
            and file.deleted_at is None
            and file.purged_at is None
            and not (file.meta or {}).get("thumb_key")
            and thumbnail_candidate(file.filename, file.content_type)
        )

    count = 0
    for file in files:
        if not missing(file):
            continue
        # Backfill and artifact registration share the owner's lock: deterministic
        # thumbnail IDs must not race into a primary-key violation.
        with user_critical_section(db, file.user_id):
            db.refresh(file)
            if missing(file):
                stage_job(db, "thumbnail", file_id=file.id)
                count += 1
            db.commit()
    db.commit()
    return {"cursor": files[-1].id if files else None, "enqueued": count}
