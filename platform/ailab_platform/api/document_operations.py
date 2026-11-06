"""Persistent heavy-work admission. No transaction is held during execution."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update

from ..config import settings
from ..models import Conversation, DocumentLane, DocumentOperation, FileObject, OutboxMessage, Run, RunAttempt, RunStatus, User


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


def source_available(db, operation, *, active=False):
    run = db.get(Run, operation.run_id, populate_existing=True)
    user = db.get(User, operation.user_id, populate_existing=True)
    if not run or not user or user.disabled_at or (active and run.status != RunStatus.RUNNING):
        return False
    conversation = db.get(Conversation, run.conversation_id, populate_existing=True) if run.conversation_id else None
    if run.conversation_id and (not conversation or conversation.deleted_at):
        return False
    if active and operation.executor_started_at and operation.attempt_token:
        attempt = db.get(RunAttempt, operation.attempt_token, populate_existing=True)
        owned = db.scalar(
            select(OutboxMessage.id)
            .where(OutboxMessage.claim_token == operation.attempt_token, OutboxMessage.claimed_until > datetime.now(UTC))
            .limit(1)
        )
        if not attempt or attempt.run_id != run.id or attempt.ended_at or not owned:
            return False
    if operation.file_id:
        file = db.get(FileObject, operation.file_id, populate_existing=True)
        if not file or file.user_id != run.user_id or file.deleted_at or file.purged_at or file.sha256 != operation.source_version:
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
        .where(DocumentOperation.lane == lane, DocumentOperation.status.in_(["pending", "running", "canceling"]))
        .order_by(DocumentOperation.created_at, DocumentOperation.id)
        .limit(34)
    ).all()
    for row in rows:
        expired = (
            aware(row.lease_until) <= now if row.status in {"running", "canceling"} and row.lease_until else aware(row.deadline) <= now
        )
        if expired or not source_available(db, row, active=True):
            # A dispatched executor retains its slot until cancellation is
            # acknowledged or the enforced executor deadline has elapsed.
            row.status = "failed" if expired else ("canceling" if row.executor_started_at else "canceled")
            row.result = {"reason": "document_operation_deadline" if expired else "document_source_unavailable"}
            if row.status != "canceling":
                row.claim_token, row.lease_until = None, None
    db.flush()
    return [row for row in rows if row.status in {"pending", "running", "canceling"}]


def request(db, run, *, lane, kind, version, binding, file_id=None, priority=0, now=None):
    now = now or datetime.now(UTC)
    identity = hashlib.sha256(json.dumps([kind, file_id, version, binding], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    lane_lock(db, lane)
    current = db.scalar(select(DocumentOperation).where(DocumentOperation.run_id == run.id, DocumentOperation.identity == identity))
    if current:
        return current
    deadline = budget(run, start=True, now=now)
    rows = expire_lane(db, lane, now)
    waiting = [row for row in rows if row.status == "pending"]
    if len(waiting) >= 32 or any(row.user_id == run.user_id for row in waiting):
        raise HTTPException(429, "document_queue_full")
    if lane == "ocr":
        state = dict((run.runtime_meta or {}).get("document_budget") or {})
        batches = int(state.get("ocr_batches", 0))
        known = {key: list(value) for key, value in state.get("ocr_pages", {}).items()}
        page_key = file_id or hashlib.sha256((binding.get("path", "") + binding.get("source_sha256", version)).encode()).hexdigest()
        requested_pages = binding.get("pages") or [1]
        if len(requested_pages) > 20 or any(type(page) is not int or page < 1 for page in requested_pages):
            raise HTTPException(422, "invalid_ocr_page_batch")
        combined = set(known.get(page_key, [])) | set(requested_pages)
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
    return row


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
    run = db.get(Run, operation.run_id, populate_existing=True)
    remaining = budget(run, now=now)
    seconds = min(120, settings.ocr_executor_timeout_seconds) if operation.lane == "ocr" else 120
    operation.status, operation.claim_token = "running", uuid4().hex
    operation.generation += 1
    operation.execution_deadline = min(aware(operation.deadline), remaining, now + timedelta(seconds=seconds))
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
