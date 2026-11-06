"""Persistent heavy-work admission. No transaction is held during execution."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update

from ..config import settings
from ..models import (
    Conversation,
    DocumentLane,
    DocumentOperation,
    DocumentOperationConsumer,
    FileObject,
    OutboxMessage,
    Run,
    RunAttempt,
    RunStatus,
    User,
)


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def budget(run, *, start=False, now=None):
    now = now or datetime.now(UTC)
    state = dict((run.runtime_meta or {}).get("document_budget") or {})
    if not state and start:
        state = {"started_at": now.isoformat(), "deadline": (now + timedelta(seconds=600)).isoformat(), "ocr_batches": 0, "ocr_pages": {}}
        run.runtime_meta = {**(run.runtime_meta or {}), "document_budget": state}
    if not state:
        return None
    deadline = datetime.fromisoformat(state["deadline"])
    if now >= deadline:
        raise HTTPException(408, "document_run_deadline")
    return deadline


def live_consumer(db, operation):
    runs = db.scalars(
        select(Run)
        .join(DocumentOperationConsumer, DocumentOperationConsumer.run_id == Run.id)
        .where(
            DocumentOperationConsumer.operation_id == operation.id,
            DocumentOperationConsumer.deadline > datetime.now(UTC),
            Run.user_id == operation.user_id,
            Run.status.in_([RunStatus.RUNNING, RunStatus.INTERRUPTED]),
        )
        .limit(50)
    ).all()
    for run in runs:
        conversation = db.get(Conversation, run.conversation_id, populate_existing=True) if run.conversation_id else None
        if run.conversation_id and (not conversation or conversation.deleted_at):
            continue
        dependency = (run.runtime_meta or {}).get("file_dependency") or {}
        if run.status == RunStatus.RUNNING or dependency.get("operation_id") == operation.id:
            return run
        # A run waiting on a Chat view/OCR still owns that operation's
        # page-render dependency. Verify the exact bounded parent edge; an
        # unrelated interrupted run cannot keep another job alive.
        parent_id = dependency.get("operation_id")
        if parent_id and operation.kind == "file_job" and operation.binding.get("job_kind") == "preview":
            parent = db.get(DocumentOperation, parent_id)
            if (
                parent
                and parent.kind in {"chat_view", "chat_ocr"}
                and parent.status in {"preparing", "pending", "running", "browser"}
                and parent.user_id == operation.user_id
                and parent.file_id == operation.file_id
                and parent.source_version == operation.source_version
                and aware(parent.deadline) > datetime.now(UTC)
                and parent.result.get("render_job_id") == operation.id
                and db.get(DocumentOperationConsumer, (run.id, parent.id)) is not None
            ):
                return run
    return None


def source_available(db, operation, *, active=False):
    user = db.get(User, operation.user_id, populate_existing=True)
    if not user or user.disabled_at:
        return False
    if operation.kind == "file_job":
        from .file_work import available

        if not available(db, operation):
            return False
        if active and operation.executor_started_at:
            job = db.get(OutboxMessage, operation.job_id, populate_existing=True)
            if (
                not job
                or job.claim_token != operation.attempt_token
                or not job.claimed_until
                or aware(job.claimed_until) <= datetime.now(UTC)
            ):
                return False
        return True
    if operation.job_id:
        run = live_consumer(db, operation)
        if not run:
            return False
    else:
        run = db.get(Run, operation.run_id, populate_existing=True)
        if not run or (active and run.status != RunStatus.RUNNING):
            return False
        conversation = db.get(Conversation, run.conversation_id, populate_existing=True) if run.conversation_id else None
        if run.conversation_id and (not conversation or conversation.deleted_at):
            return False
    if active and operation.executor_started_at and operation.attempt_token:
        owned = db.scalar(
            select(OutboxMessage.id)
            .where(OutboxMessage.claim_token == operation.attempt_token, OutboxMessage.claimed_until > datetime.now(UTC))
            .limit(1)
        )
        if not owned or (operation.job_id and owned != operation.job_id):
            return False
        if not operation.job_id:
            attempt = db.get(RunAttempt, operation.attempt_token, populate_existing=True)
            if not attempt or attempt.run_id != run.id or attempt.ended_at:
                return False
    if operation.file_id:
        file = db.get(FileObject, operation.file_id, populate_existing=True)
        if (
            not file
            or file.user_id != run.user_id
            or file.deleted_at
            or file.purged_at
            or file.sha256 != operation.source_version
            or file.status != "ready"
            or (file.meta or {}).get("scan", {}).get("verdict") != "clean"
        ):
            return False
        if operation.kind == "native_scope":
            from .native_preparation import native_state

            if native_state(file).get("derivation_id") != operation.binding.get("base_derivation_id"):
                return False
    return True


def lane_lock(db, name):
    if name not in {"render", "ocr"}:
        raise HTTPException(422, "invalid_document_lane")
    db.execute(update(DocumentLane).where(DocumentLane.name == name).values(name=name))
    row = db.get(DocumentLane, name, populate_existing=True)
    if row is None:
        raise HTTPException(503, "document_scheduler_not_migrated")
    return row


def expire_lane(db, lane, now):
    # One active slot plus a maximum of 32 waiting operations; work is bounded.
    rows = db.scalars(
        select(DocumentOperation)
        .where(DocumentOperation.lane == lane, DocumentOperation.status.in_(["pending", "running", "canceling", "browser", "preparing"]))
        .order_by(DocumentOperation.created_at, DocumentOperation.id)
        .limit(34)
    ).all()
    for row in rows:
        expires = (
            aware(row.lease_until)
            if row.status in {"running", "canceling"} and row.lease_until
            else min(aware(row.deadline), aware(row.execution_deadline) if row.execution_deadline else aware(row.deadline))
        )
        expired = expires <= now
        if expired or not source_available(db, row, active=True):
            # A dispatched executor retains its slot until cancellation is
            # acknowledged or the enforced executor deadline has elapsed.
            row.status = "failed" if expired else ("canceling" if row.executor_started_at else "canceled")
            reason = "document_operation_deadline" if expired else "document_source_unavailable"
            if row.kind == "chat_view":
                from .document_visual import fail

                fail(db, row, reason)
            else:
                row.result = {"reason": reason}
            if row.status != "canceling":
                row.claim_token, row.lease_until = None, None
    db.flush()
    return [row for row in rows if row.status in {"pending", "running", "canceling"}]


def request(db, run, *, lane, kind, version, binding, file_id=None, priority=0, now=None, share=False):
    now = now or datetime.now(UTC)
    identity = hashlib.sha256(
        json.dumps([kind, file_id, version, binding.get("identity", binding)], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    lane_lock(db, lane)
    current = db.scalar(select(DocumentOperation).where(DocumentOperation.run_id == run.id, DocumentOperation.identity == identity))
    if current is None and share:
        current = db.scalar(
            select(DocumentOperation)
            .join(DocumentOperationConsumer, DocumentOperationConsumer.operation_id == DocumentOperation.id)
            .where(DocumentOperationConsumer.run_id == run.id, DocumentOperation.identity == identity)
            .limit(1)
        )
    if current:
        if share:
            attach_consumer(db, run, current, now=now)
        return current
    deadline = budget(run, start=True, now=now)
    rows = expire_lane(db, lane, now)
    if share and file_id:
        shared = db.scalar(
            select(DocumentOperation)
            .where(
                DocumentOperation.file_id == file_id,
                DocumentOperation.identity == identity,
                DocumentOperation.user_id == run.user_id,
                DocumentOperation.status.in_(["pending", "running"]),
            )
            .limit(1)
        )
        if shared:
            attach_consumer(db, run, shared, now=now)
            return shared
    waiting = [row for row in rows if row.status in {"pending", "browser", "preparing"}]
    if len(waiting) >= 32 or any(row.user_id == run.user_id for row in waiting):
        raise HTTPException(429, "document_queue_full")
    row = DocumentOperation(
        run_id=run.id,
        user_id=run.user_id,
        file_id=file_id,
        identity=identity,
        lane=lane,
        kind=kind,
        source_version=version,
        binding=binding,
        priority=priority,
        status="pending",
        deadline=deadline,
        result={},
    )
    db.add(row)
    db.flush()
    if share:
        attach_consumer(db, run, row, now=now)
    return row


def attach_consumer(db, run, operation, *, now=None):
    deadline = budget(run, start=True, now=now)
    if db.get(DocumentOperationConsumer, (run.id, operation.id)) is None:
        count = db.scalars(
            select(DocumentOperationConsumer.run_id).where(DocumentOperationConsumer.operation_id == operation.id).limit(50)
        ).all()
        if len(count) >= 50:
            raise HTTPException(429, "document_consumer_limit")
        db.add(DocumentOperationConsumer(run_id=run.id, operation_id=operation.id, deadline=deadline))
        db.flush()


def charge_ocr(run, operation):
    binding, version, file_id = operation.binding, operation.source_version, operation.file_id
    state = dict((run.runtime_meta or {}).get("document_budget") or {})
    batches = int(state.get("ocr_batches", 0))
    known = {key: [str(item) for item in value] for key, value in state.get("ocr_pages", {}).items()}
    page_key = file_id or hashlib.sha256((binding.get("path", "") + binding.get("source_sha256", version)).encode()).hexdigest()
    requested_pages = binding.get("pages") or [1]
    if len(requested_pages) > 20 or any(type(page) is not int or page < 1 for page in requested_pages):
        raise HTTPException(422, "invalid_ocr_page_batch")
    source_pages = (
        (operation.result or {}).get("work_pages") or binding.get("source_pages") or ["page:" + str(page) for page in requested_pages]
    )
    if len(source_pages) > 20 or any(not isinstance(page, str) or len(page) > 120 for page in source_pages):
        raise HTTPException(422, "invalid_ocr_page_batch")
    combined = set(known.get(page_key, [])) | set(source_pages)
    unique_pages = sum(len(value) for key, value in known.items() if key != page_key) + len(combined)
    per_file = dict(state.get("ocr_file_batches") or {})
    if batches >= 4 or per_file.get(page_key, 0) >= 4 or unique_pages > 80:
        raise HTTPException(429, "document_ocr_work_limit")
    known[page_key] = sorted(combined)
    per_file[page_key] = per_file.get(page_key, 0) + 1
    run.runtime_meta = {
        **(run.runtime_meta or {}),
        "document_budget": {
            **state,
            "ocr_batches": batches + 1,
            "ocr_pages": known,
            "ocr_file_batches": per_file,
        },
    }


def claim(db, operation, *, now=None):
    now = now or datetime.now(UTC)
    lane = lane_lock(db, operation.lane)
    rows = expire_lane(db, operation.lane, now)
    if operation.status != "pending" or any(row.status in {"running", "canceling"} for row in rows):
        return operation
    waiting = [row for row in rows if row.status == "pending"]
    # A user with one running job may wait once; it cannot leapfrog another
    # user's queued work. Aging overrides priority after one minute.
    others = [row for row in waiting if row.user_id != lane.last_user_id]
    choices = others or waiting
    selected = (
        min(
            choices,
            key=lambda row: (
                0 if now - aware(row.created_at) >= timedelta(seconds=60) else 1,
                -row.priority,
                aware(row.created_at),
                row.id,
            ),
        )
        if choices
        else None
    )
    if selected is not operation:
        return operation
    run = live_consumer(db, operation) if operation.job_id else db.get(Run, operation.run_id, populate_existing=True)
    remaining = aware(operation.deadline) if operation.job_id else budget(run, now=now)
    seconds = min(120, settings.ocr_executor_timeout_seconds) if operation.lane == "ocr" else 120
    if operation.lane == "ocr" and operation.execution_deadline is None and (operation.kind != "file_job" or run is not None):
        try:
            charge_ocr(run, operation)
        except HTTPException as exc:
            operation.status, operation.result = "failed", {"reason": exc.detail}
            return operation
    operation.status, operation.claim_token = "running", uuid4().hex
    operation.generation += 1
    operation.execution_deadline = min(
        aware(operation.deadline),
        remaining,
        now + timedelta(seconds=seconds),
        aware(operation.execution_deadline) if operation.execution_deadline else aware(operation.deadline),
    )
    # The executor's deadline stops inference first. Its bounded process reap
    # must finish before an abandoned owner can free the global slot.
    operation.lease_until = operation.execution_deadline + timedelta(seconds=5)
    lane.last_user_id = operation.user_id
    return operation


def active(db, operation_id, token, *, run_id=None):
    row = db.get(DocumentOperation, operation_id, populate_existing=True)
    if not row or (run_id is not None and row.run_id != run_id):
        raise HTTPException(404, "document_operation_unavailable")
    if (
        row.status != "running"
        or not token
        or row.claim_token != token
        or not row.lease_until
        or aware(row.lease_until) <= datetime.now(UTC)
        or (row.execution_deadline and aware(row.execution_deadline) <= datetime.now(UTC))
        or not source_available(db, row, active=True)
    ):
        raise HTTPException(409, "document_operation_superseded")
    return row


def receipt(row):
    return {
        "operation_id": row.id,
        "status": row.status,
        "generation": row.generation,
        "deadline": aware(row.deadline).isoformat(),
        **(
            {"execution_deadline": aware(row.execution_deadline or row.lease_until).isoformat(), "claim_token": row.claim_token}
            if row.status == "running"
            else {}
        ),
        **({"result": row.result} if row.status in {"ready", "failed", "canceled"} else {}),
    }


def maintenance(db):
    """Expire bounded queues even when no new caller arrives to drive a lane."""
    files = set()
    for lane in ("render", "ocr"):
        lane_lock(db, lane)
        rows = db.scalars(
            select(DocumentOperation)
            .where(
                DocumentOperation.lane == lane, DocumentOperation.status.in_(["pending", "running", "canceling", "browser", "preparing"])
            )
            .limit(34)
        ).all()
        expire_lane(db, lane, datetime.now(UTC))
        files.update(row.file_id for row in rows if row.file_id and row.status in {"failed", "canceled"})
        db.commit()
    # Never take a user lock while holding the lane lock: admission uses the
    # user-then-lane order. Wakeups happen only after the queue transaction ends.
    from ..locks import user_critical_section
    from .file_dependencies import wake_file

    for file_id in files:
        file = db.get(FileObject, file_id)
        if file:
            with user_critical_section(db, file.user_id):
                db.refresh(file)
                from .native_preparation import native_state

                state = native_state(file)
                operation = db.get(DocumentOperation, state.get("job_id")) if state.get("job_id") else None
                if (
                    operation
                    and operation.kind == "file_job"
                    and operation.status in {"failed", "canceled"}
                    and state.get("status") == "pending"
                ):
                    file.meta = {
                        **file.meta,
                        "representations": {
                            **file.meta.get("representations", {}),
                            "native_text": {**state, "status": "failed", "reason": (operation.result or {}).get("reason", "native_failed")},
                        },
                    }
                preview_job = (file.meta or {}).get("preview_job_id")
                preview_operation = db.get(DocumentOperation, preview_job) if preview_job else None
                if preview_operation and preview_operation.kind == "file_job" and preview_operation.status in {"failed", "canceled"}:
                    preview = (file.meta or {}).get("preview") or {}
                    if preview.get("status") == "pending":
                        file.meta = {
                            **file.meta,
                            "preview": {**preview, "status": "failed", "reason": preview_operation.result.get("reason", "render_failed")},
                        }
                wake_file(db, file)
                db.commit()
