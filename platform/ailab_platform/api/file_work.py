"""Admit file-owned heavy jobs into the same persistent document lanes as run tools."""

import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_db
from ..locks import user_critical_section
from ..models import DocumentOperation, DocumentPreparation, FileObject, OutboxMessage
from ..service_auth import require_scope
from . import document_operations as operations


def available(db, operation):
    # Do not refresh away a cancellation/deadline changed by this transaction.
    db.flush()
    file = db.get(FileObject, operation.file_id, populate_existing=True)
    job = db.get(OutboxMessage, operation.job_id, populate_existing=True)
    if (
        not file
        or file.user_id != operation.user_id
        or file.deleted_at
        or file.purged_at
        or file.sha256 != operation.source_version
        or not job
        or job.payload.get("file_id") != file.id
        or job.payload.get("kind") != operation.binding["job_kind"]
    ):
        return False
    if operation.binding["job_kind"] == "native":
        state = ((file.meta or {}).get("representations") or {}).get("native_text") or {}
        if state.get("status") != "pending" or state.get("job_id") != operation.job_id:
            return False
    if operation.binding["job_kind"] == "preview" and (file.meta or {}).get("preview_job_id") != operation.job_id:
        return False
    if operation.binding["job_kind"] == "ocr" and (file.meta or {}).get("ocr_job_id") != operation.job_id:
        return False
    if operation.binding.get("file_demand") or operations.live_consumer(db, operation):
        return True
    preparations = db.scalars(
        select(DocumentPreparation)
        .where(
            DocumentPreparation.id.in_(operation.binding.get("preparations", [])),
            DocumentPreparation.user_id == file.user_id,
            DocumentPreparation.deadline > datetime.now(UTC),
        )
        .limit(50)
    ).all()
    for preparation in preparations:
        if not preparation.run_id:
            return True
        from ..models import Conversation, Run, RunStatus

        run = db.get(Run, preparation.run_id, populate_existing=True)
        if not run or run.status not in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.INTERRUPTED}:
            continue
        conversation = db.get(Conversation, run.conversation_id, populate_existing=True) if run.conversation_id else None
        if not run.conversation_id or (conversation and not conversation.deleted_at):
            return True
    return False


def attach(db, operation, *, run=None, preparation=None):
    if run:
        operations.attach_consumer(db, run, operation)
    if preparation:
        ids = list(operation.binding.get("preparations", []))
        if preparation.id not in ids:
            if len(ids) >= 50:
                raise HTTPException(429, "document_consumer_limit")
            ids.append(preparation.id)
            operation.binding = {**operation.binding, "preparations": ids}
    if not run and not preparation:
        operation.binding = {**operation.binding, "file_demand": True}


def register(db, file, job, *, lane="render", run=None, preparation=None):
    operations.lane_lock(db, lane)
    row = db.get(DocumentOperation, job.id, populate_existing=True)
    if row:
        attach(db, row, run=run, preparation=preparation)
        return row
    now = datetime.now(UTC)
    deadline = min(datetime.fromisoformat(job.payload["deadline"]), now + timedelta(seconds=600))
    if run:
        deadline = min(deadline, operations.budget(run, start=True))
    if preparation:
        deadline = min(deadline, operations.aware(preparation.deadline))
    waiting = [item for item in operations.expire_lane(db, lane, now) if item.status == "pending"]
    if len(waiting) >= 32 or any(item.user_id == file.user_id for item in waiting):
        raise HTTPException(429, "document_queue_full")
    row = DocumentOperation(
        id=job.id,
        user_id=file.user_id,
        file_id=file.id,
        job_id=job.id,
        source_version=file.sha256,
        identity=hashlib.sha256((job.id + file.sha256).encode()).hexdigest(),
        lane=lane,
        kind="file_job",
        binding={"job_kind": job.payload["kind"], "preparations": []},
        status="pending",
        deadline=deadline,
        result={},
        priority=1,
    )
    db.add(row)
    db.flush()
    attach(db, row, run=run, preparation=preparation)
    job.payload = {**job.payload, "deadline": deadline.isoformat()}
    return row


def start(db, file, job, attempt):
    row = db.get(DocumentOperation, job.id, populate_existing=True)
    if row is None:
        row = register(db, file, job)
    if row.status in {"ready", "failed", "canceled"}:
        return row
    if row.executor_started_at:
        if row.attempt_token != attempt:
            row.status, row.result = "canceling", {"reason": "document_dispatch_uncertain"}
        else:
            raise HTTPException(409, "document_operation_already_dispatched")
        return row
    operations.claim(db, row)
    if row.status == "running":
        row.attempt_token, row.executor_started_at = attempt, datetime.now(UTC)
    return row


def finish(db, job_id, attempt, *, reason=None):
    row = db.get(DocumentOperation, job_id, populate_existing=True)
    if not row or row.kind != "file_job" or row.attempt_token != attempt:
        raise HTTPException(409, "document_operation_superseded")
    operations.active(db, row.id, row.claim_token)
    row.status, row.claim_token, row.lease_until = "failed" if reason else "ready", None, None
    row.result = {"reason": reason} if reason else {}


router = APIRouter(prefix="/internal/file-work")


def job_for_attempt(db, job_id, attempt):
    job = db.get(OutboxMessage, job_id, populate_existing=True)
    if (
        not job
        or job.payload.get("kind") not in {"preview", "native", "ocr"}
        or not attempt
        or job.claim_token != attempt
        or not job.claimed_until
        or operations.aware(job.claimed_until) <= datetime.now(UTC)
    ):
        raise HTTPException(409, "document_job_superseded")
    file = db.get(FileObject, job.payload["file_id"], populate_existing=True)
    if not file or file.deleted_at or file.purged_at:
        raise HTTPException(404, "document_unavailable")
    return job, file


@router.post("/{job_id}/start", dependencies=[Depends(require_scope("files:preview_write"))])
def start_preview(job_id: str, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    job, file = job_for_attempt(db, job_id, x_run_attempt)
    if job.payload["kind"] != "preview":
        raise HTTPException(422, "document_job_kind_mismatch")
    with user_critical_section(db, file.user_id):
        job, file = job_for_attempt(db, job_id, x_run_attempt)
        row = start(db, file, job, x_run_attempt)
        if row.status == "running":
            file.meta = {
                **file.meta,
                "preview_execution": {"operation_id": row.id, "deadline": operations.aware(row.execution_deadline).isoformat()},
            }
        elif row.status in {"failed", "canceled", "canceling"} and file.meta.get("preview_job_id") == job.id:
            file.meta = {
                **file.meta,
                "preview": {
                    **file.meta.get("preview", {}),
                    "status": "failed",
                    "reason": row.result.get("reason", "document_operation_failed"),
                },
            }
            from .file_dependencies import wake_file

            wake_file(db, file)
        db.commit()
        return {**operations.receipt(row), **({"status": "failed"} if row.status == "canceling" else {})}


@router.post("/{job_id}/owned", dependencies=[Depends(require_scope("files:read_for_preview", "files:preview_write"))])
def owned(job_id: str, db: Session = Depends(get_db)):
    row = db.get(DocumentOperation, job_id, populate_existing=True)
    if not row or row.kind != "file_job" or row.binding.get("job_kind") != "preview":
        raise HTTPException(409, "document_operation_superseded")
    with user_critical_section(db, row.user_id):
        db.refresh(row)
        operations.active(db, row.id, row.claim_token)
        return {"owned": True, "deadline": operations.aware(row.execution_deadline).isoformat()}


@router.post("/{job_id}/defer", dependencies=[Depends(require_scope("files:preview_write"))])
def defer_preview(job_id: str, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    job, file = job_for_attempt(db, job_id, x_run_attempt)
    if job.payload["kind"] != "preview":
        raise HTTPException(422, "document_job_kind_mismatch")
    with user_critical_section(db, file.user_id):
        operations.lane_lock(db, "render")
        row = db.get(DocumentOperation, job_id, populate_existing=True)
        if not row or row.attempt_token != x_run_attempt:
            raise HTTPException(409, "document_operation_superseded")
        operations.active(db, row.id, row.claim_token)
        row.status, row.claim_token, row.lease_until = "pending", None, None
        row.executor_started_at, row.attempt_token = None, None
        # Preserve the original execution deadline across capacity redelivery.
        db.commit()
        return operations.receipt(row)


class Stopped(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt: str = Field(min_length=1, max_length=40)


@router.post("/{operation_id}/stopped", dependencies=[Depends(require_scope("files:read_for_document", "files:read_for_preview"))])
def execution_stopped(operation_id: str, body: Stopped, db: Session = Depends(get_db)):
    row = db.get(DocumentOperation, operation_id, populate_existing=True)
    if not row:
        return {"status": "gone"}
    with user_critical_section(db, row.user_id):
        operations.lane_lock(db, row.lane)
        db.refresh(row)
        if row.attempt_token != body.attempt:
            raise HTTPException(409, "document_operation_superseded")
        if row.status in {"running", "canceling"}:
            row.status, row.claim_token, row.lease_until = "failed", None, None
            row.result = {"reason": "document_execution_stopped"}
            file = db.get(FileObject, row.file_id, populate_existing=True)
            if file and not file.purged_at:
                state = ((file.meta or {}).get("representations") or {}).get("native_text") or {}
                if state.get("job_id") == row.job_id:
                    file.meta = {
                        **file.meta,
                        "representations": {
                            **file.meta.get("representations", {}),
                            "native_text": {**state, "status": "failed", "reason": "document_execution_stopped"},
                        },
                    }
                if file.meta.get("preview_job_id") == row.job_id:
                    file.meta = {
                        **file.meta,
                        "preview": {**file.meta.get("preview", {}), "status": "failed", "reason": "document_execution_stopped"},
                    }
                from .file_dependencies import wake_file

                wake_file(db, file)
            db.commit()
        return operations.receipt(row)
