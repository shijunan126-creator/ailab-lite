"""Pre-run identity is persisted before starting work; HTTP retries never renew its budget."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select

from ..models import DocumentPreparation
from .document_operations import aware


def get_preparation(db, user, files, model, prompt, send_id):
    # Sessions disable autoflush. An adopted send identity must be visible to
    # the next request in this transaction, including its uniqueness check.
    db.flush()
    identity = hashlib.sha256(
        json.dumps([model, [(file.id, file.sha256) for file in files]], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    explicit_send_id = send_id
    send_id = send_id or "legacy:" + identity
    row = db.scalar(select(DocumentPreparation).where(DocumentPreparation.user_id == user.id, DocumentPreparation.send_id == send_id))
    if row and row.identity != identity:
        raise HTTPException(409, "document_preparation_identity_changed")
    if row is None and explicit_send_id:
        legacy = db.scalar(
            select(DocumentPreparation).where(
                DocumentPreparation.user_id == user.id,
                DocumentPreparation.send_id == "legacy:" + identity,
                DocumentPreparation.run_id.is_(None),
            )
        )
        if legacy:
            legacy.send_id = send_id
            row = legacy
            db.flush()
    if row is None:
        row = DocumentPreparation(
            user_id=user.id, send_id=send_id, identity=identity, expires_at=datetime.now(UTC) + timedelta(days=30), plan={}
        )
        db.add(row)
        db.flush()
    return row


def start_budget(db, row):
    now = datetime.now(UTC)
    if row.started_at is None:
        row.started_at, row.deadline = now, now + timedelta(seconds=600)
        db.commit()  # committed before any outbox job can execute
    if aware(row.deadline) <= now:
        raise HTTPException(408, {"code": "chat_attachment_deadline", "message": "本次附件准备已超时，请重新发送"})
    return aware(row.deadline)


def bind(db, run, plan):
    preparation = db.get(DocumentPreparation, plan["preparation_id"])
    if preparation:
        if preparation.user_id != run.user_id or preparation.plan != plan:
            raise HTTPException(409, "document_preparation_identity_changed")
        if preparation.run_id and preparation.run_id != run.id:
            raise HTTPException(409, "document_preparation_already_used")
        preparation.run_id = run.id
        if preparation.send_id == "legacy:" + preparation.identity:
            preparation.send_id += ":" + run.id
    # Queue/history snapshots are API-owned and retain their original budget
    # after the small preflight row expires; model input never supplies a plan.
    saved = plan.get("budget")
    if not saved and preparation and preparation.started_at:
        saved = {"started_at": aware(preparation.started_at).isoformat(), "deadline": aware(preparation.deadline).isoformat()}
    if saved:
        current = (run.runtime_meta or {}).get("document_budget") or {}
        merged = {**saved, "ocr_batches": 0, "ocr_pages": {}, **current}
        if current:
            for field in ("started_at", "deadline"):
                merged[field] = min(datetime.fromisoformat(current[field]), datetime.fromisoformat(saved[field])).isoformat()
        run.runtime_meta = {**(run.runtime_meta or {}), "document_budget": merged}


def maintenance(db):
    ids = list(
        db.scalars(
            select(DocumentPreparation.id)
            .where(DocumentPreparation.expires_at <= datetime.now(UTC))
            .order_by(DocumentPreparation.expires_at, DocumentPreparation.id)
            .limit(100)
        )
    )
    if ids:
        db.execute(delete(DocumentPreparation).where(DocumentPreparation.id.in_(ids)))
        db.commit()
    return len(ids)
