"""Lazy native representation jobs; security readiness is never changed by parsing."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..contracts.file_types import OFFICE_SUFFIXES, TEXT_SUFFIXES
from ..deps import get_db
from ..locks import user_critical_section
from ..models import FileDerivation, FileObject, OutboxMessage
from ..native_text import PARSER_NATIVE, PARSER_V4
from ..service_auth import require_scope
from ..storage import get_storage
from . import derived_assets as assets
from .document_sources import index_document
from .jobs import stage_job
from .routes_derived_assets import Completion

router = APIRouter(prefix="/internal/files", tags=["internal"])
KINDS = OFFICE_SUFFIXES | TEXT_SUFFIXES


def native_kind(file):
    actual = ((file.meta or {}).get("safety") or {}).get("kind")
    if actual in {"pdf", "docx", "pptx", "xlsx"}:
        return actual
    if actual in {"zip", "png", "jpeg", "gif", "webp"}:
        return None
    suffix = PurePath(file.filename).suffix.lower()
    if suffix in OFFICE_SUFFIXES:
        return suffix[1:]
    return "text" if suffix in TEXT_SUFFIXES or file.content_type.startswith("text/") else None


def native_state(file):
    return ((file.meta or {}).get("representations") or {}).get("native_text") or {"status": "absent"}


def request_native(db, file, *, full_pdf=False, run=None, preparation=None):
    if file.deleted_at or file.purged_at or file.status != "ready" or (file.meta or {}).get("scan", {}).get("verdict") != "clean":
        raise HTTPException(404, "document_unavailable")
    if native_kind(file) is None:
        raise HTTPException(422, "native_format_unsupported")
    if file.size > 20 * 1024**2:
        raise HTTPException(413, "document_input_limit")
    current = native_state(file)
    full_pdf = full_pdf or current.get("pdf_strategy") == "full" or current.get("parser") == PARSER_V4
    if current.get("version") == file.sha256 and current.get("parser") in {PARSER_NATIVE, PARSER_V4}:
        if current["status"] == "failed":
            return current
        if current["status"] == "ready":
            derivation = db.get(FileDerivation, current.get("derivation_id"))
            if (
                derivation
                and derivation.file_id == file.id
                and derivation.status in {"ready", "partial"}
                and (file.meta or {}).get("document_manifest_key") == derivation.manifest_key
                and not (
                    full_pdf and native_kind(file) == "pdf" and current.get("pdf_strategy") == "probe" and derivation.status == "partial"
                )
            ):
                return current
        if current["status"] == "pending" and (existing_job := db.get(OutboxMessage, current.get("job_id"))):
            from .file_work import register

            register(db, file, existing_job, run=run, preparation=preparation)
            return current
    job_id = uuid4().hex
    state = {
        "status": "pending",
        "version": file.sha256,
        "parser": PARSER_NATIVE,
        "job_id": job_id,
        "pdf_strategy": "full" if full_pdf else "probe",
    }
    file.meta = {**(file.meta or {}), "representations": {**(file.meta or {}).get("representations", {}), "native_text": state}}
    job = stage_job(db, "native", file_id=file.id, job_id=job_id)
    job.payload = {**job.payload, "deadline": (datetime.now(UTC) + timedelta(seconds=600)).isoformat()}
    from .file_work import register

    register(db, file, job, run=run, preparation=preparation)
    db.flush()
    return state


def claimed_file(db, file_id, job_id, attempt):
    file = db.get(FileObject, file_id, populate_existing=True)
    job = db.get(OutboxMessage, job_id, populate_existing=True)
    if not file or file.deleted_at or file.purged_at:
        raise HTTPException(404, "document_unavailable")
    state = native_state(file)
    if (
        file.status != "ready"
        or state.get("status") != "pending"
        or state.get("job_id") != job_id
        or state.get("version") != file.sha256
        or not job
        or job.payload.get("kind") != "native"
        or job.payload.get("file_id") != file.id
        or not attempt
        or job.claim_token != attempt
        or not job.claimed_until
        or assets.aware(job.claimed_until) <= datetime.now(UTC)
    ):
        raise HTTPException(409, "native_generation_stale")
    return file


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=40)


class Complete(Start):
    manifest_key: str | None = Field(default=None, max_length=600)
    receipts: list[Completion] = Field(default_factory=list, max_length=2)
    reason: str | None = Field(default=None, max_length=80)


@router.post("/{file_id}/native/start", dependencies=[Depends(require_scope("files:patch_processing"))])
def start(file_id: str, body: Start, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    file = claimed_file(db, file_id, body.job_id, x_run_attempt)
    with user_critical_section(db, file.user_id):
        file = claimed_file(db, file_id, body.job_id, x_run_attempt)
        from .document_operations import aware, receipt
        from .file_work import start as start_work

        job = db.get(OutboxMessage, body.job_id)
        operation = start_work(db, file, job, x_run_attempt)
        if operation.status != "running":
            if operation.status in {"failed", "canceled", "canceling"}:
                file.meta = {
                    **file.meta,
                    "representations": {
                        **file.meta.get("representations", {}),
                        "native_text": {
                            **native_state(file),
                            "status": "failed",
                            "reason": (operation.result or {}).get("reason", "native_failed"),
                        },
                    },
                }
                from .file_dependencies import wake_file

                wake_file(db, file)
            db.commit()
            return {**receipt(operation), **({"status": "failed"} if operation.status == "canceling" else {})}
        # Broker's channel remains tied to this attempt and persistent slot.
        file.meta = {
            **file.meta,
            "document_job_id": x_run_attempt,
            "document_profile": "chat-full" if native_state(file).get("pdf_strategy") == "full" else "chat",
            "document_kind": native_kind(file),
            "document_operation_id": operation.id,
            "document_deadline": aware(operation.execution_deadline).isoformat(),
        }
        db.commit()
    return {
        "job_id": x_run_attempt,
        "version": file.sha256,
        "filename": file.filename,
        "kind": native_kind(file),
        "content_type": file.content_type,
        "size": file.size,
        "execution_deadline": aware(operation.execution_deadline).isoformat(),
    }


@router.post("/{file_id}/native/complete", dependencies=[Depends(require_scope("files:patch_processing"))])
def complete(file_id: str, body: Complete, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    fingerprint = hashlib.sha256(((x_run_attempt or "") + body.model_dump_json()).encode()).hexdigest()
    existing = db.get(FileObject, file_id, populate_existing=True)
    if existing:
        with user_critical_section(db, existing.user_id):
            db.refresh(existing)
            recorded = native_state(existing)
            if (
                not existing.deleted_at
                and not existing.purged_at
                and recorded.get("version") == existing.sha256
                and recorded.get("status") in {"ready", "failed"}
                and recorded.get("completion_fingerprint") == fingerprint
            ):
                return recorded
    file = claimed_file(db, file_id, body.job_id, x_run_attempt)
    owner = file.user_id
    manifest = None
    if not body.reason:
        prefix = f"derived/{file.id}/office/{body.job_id}-{x_run_attempt}/"
        expected = {prefix + "manifest.json", prefix + "text.txt"}
        if body.manifest_key != prefix + "manifest.json" or {r.key for r in body.receipts} != expected or len(body.receipts) != 2:
            raise HTTPException(422, "invalid_native_completion")
        # Object I/O must not hold the publication lock or a database transaction.
        db.rollback()
        storage = get_storage()
        if storage.size(body.manifest_key) > 4 * 1024**2:
            raise HTTPException(413, "native_structure_limit")
        try:
            manifest = json.loads(storage.get_bytes(body.manifest_key))
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise HTTPException(422, "invalid_native_manifest") from exc
        if not isinstance(manifest, dict) or manifest.get("parser") != PARSER_NATIVE or manifest.get("version") != 2:
            raise HTTPException(422, "invalid_native_manifest")
        if manifest.get("chars", 200001) > 200000 or len(manifest.get("units", [])) > 10000 or (manifest.get("cells") or 0) > 200000:
            raise HTTPException(413, "native_structure_limit")
    with user_critical_section(db, owner):
        file = db.get(FileObject, file_id, populate_existing=True)
        recorded = native_state(file) if file else {}
        if (
            file
            and not file.deleted_at
            and not file.purged_at
            and recorded.get("version") == file.sha256
            and recorded.get("status") in {"ready", "failed"}
            and recorded.get("completion_fingerprint") == fingerprint
        ):
            return recorded
        file = claimed_file(db, file_id, body.job_id, x_run_attempt)
        from .file_work import finish as finish_work

        finish_work(db, body.job_id, x_run_attempt, reason=body.reason)
        state = native_state(file)
        if body.reason:
            if body.manifest_key or body.receipts:
                raise HTTPException(422, "invalid_native_completion")
            state = {**state, "status": "failed", "reason": body.reason}
        else:
            prefix = f"derived/{file.id}/office/{body.job_id}-{x_run_attempt}/"
            expected = {prefix + "manifest.json", prefix + "text.txt"}
            if body.manifest_key != prefix + "manifest.json" or {r.key for r in body.receipts} != expected or len(body.receipts) != 2:
                raise HTTPException(422, "invalid_native_completion")
            for receipt in body.receipts:
                row = assets.publish(db, receipt.key, receipt.token)
                if row.job_id != body.job_id or row.attempt_token != x_run_attempt or row.kind != "native":
                    raise HTTPException(409, "native_generation_stale")
            derivation = index_document(db, file, body.manifest_key, manifest=manifest)
            old = file.meta.get("document_manifest_key")
            if old and old != body.manifest_key:
                old_derivation = db.scalar(select(FileDerivation).where(FileDerivation.manifest_key == old))
                if old_derivation:
                    old_derivation.status = "retired"
                for key in [old, old.rsplit("/", 1)[0] + "/text.txt"]:
                    assets.retire(db, key)
                # Scope indexes belong to the previous base generation too.
                # Retire their object inventory with the base publication; the
                # indexed collector removes their business rows after grace.
                from sqlalchemy import update

                from ..models import DerivedAsset

                now = datetime.now(UTC)
                db.execute(
                    update(DerivedAsset)
                    .where(
                        DerivedAsset.file_id == file.id,
                        DerivedAsset.kind == "native",
                        DerivedAsset.state == "ready",
                        DerivedAsset.key.not_in(expected),
                        DerivedAsset.retired_at.is_(None),
                    )
                    .values(retired_at=now, expires_at=now + timedelta(seconds=settings.derived_generation_grace_seconds))
                )
            file.meta = {
                **file.meta,
                "document_manifest_key": body.manifest_key,
                "text_key": prefix + "text.txt",
                "extract": {
                    "kind": manifest["kind"],
                    "parser": PARSER_NATIVE,
                    "coverage": manifest["status"],
                    "chars": manifest["chars"],
                    "source_chars": manifest.get("source_chars", manifest["chars"]),
                    "source_blocks": manifest.get("source_blocks", len(manifest["units"])),
                    "cells": manifest.get("cells"),
                    "units": manifest["total_units"],
                    "truncated": manifest["status"] != "ready",
                },
            }
            state = {**state, "status": "ready", "derivation_id": derivation.id, "coverage": manifest["status"]}
        state = {**state, "completion_fingerprint": fingerprint}
        file.meta = {**file.meta, "representations": {**file.meta.get("representations", {}), "native_text": state}}
        from .file_dependencies import wake_file

        wake_file(db, file)
        db.commit()
    return state


@router.post("/{file_id}/native/owned", dependencies=[Depends(require_scope("files:read_for_document", "files:patch_processing"))])
def owned(file_id: str, body: Start, db: Session = Depends(get_db)):
    from ..models import DocumentOperation
    from . import document_operations as operations

    file = db.get(FileObject, file_id, populate_existing=True)
    operation_id = (file.meta or {}).get("document_operation_id") if file else None
    row = db.get(DocumentOperation, operation_id, populate_existing=True) if operation_id else None
    if not file or not row or row.file_id != file.id or row.attempt_token != body.job_id:
        raise HTTPException(409, "document_operation_superseded")
    with user_critical_section(db, file.user_id):
        db.refresh(row)
        operations.active(db, row.id, row.claim_token)
        return {"owned": True, "deadline": operations.aware(row.execution_deadline).isoformat()}
