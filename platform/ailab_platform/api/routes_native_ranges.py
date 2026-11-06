"""Durable native range execution, using the same bounded two-object transport."""

import hashlib
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..contracts.document_input import ReadDocument
from ..deps import get_db
from ..locks import user_critical_section
from ..models import DocumentNativeRange, DocumentOperation, FileObject, OutboxMessage
from ..native_text import PARSER_NATIVE
from ..service_auth import require_scope
from ..storage import get_storage
from . import derived_assets as assets
from . import document_operations as operations
from .document_ocr import consumer_file, scope_key
from .document_sources import index_document
from .document_text import current_index
from .native_preparation import Complete, Start
from .native_ranges import missing_scope

router = APIRouter(prefix="/internal/files", dependencies=[Depends(require_scope("files:patch_processing"))])


def claimed(db, file_id, operation_id, attempt):
    row = db.get(DocumentOperation, operation_id, populate_existing=True)
    job = db.get(OutboxMessage, operation_id, populate_existing=True)
    if (
        not row
        or row.file_id != file_id
        or row.kind != "native_scope"
        or row.job_id != operation_id
        or not job
        or job.payload.get("kind") != "native_scope"
        or not attempt
        or job.claim_token != attempt
        or not job.claimed_until
        or operations.aware(job.claimed_until) <= datetime.now(UTC)
    ):
        raise HTTPException(409, "native_scope_superseded")
    return row


def fail(db, row, reason):
    row.status, row.claim_token, row.lease_until = "failed", None, None
    row.result = {"reason": reason}
    wake(db, row)


def wake(db, row):
    from .file_dependencies import wake_file

    file = db.get(FileObject, row.file_id)
    if file:
        wake_file(db, file)


@router.post("/{file_id}/native/scope/start")
def start(file_id: str, body: Start, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    row = claimed(db, file_id, body.job_id, x_run_attempt)
    with user_critical_section(db, row.user_id):
        row = claimed(db, file_id, body.job_id, x_run_attempt)
        if row.status in {"ready", "failed", "canceled"}:
            return operations.receipt(row)
        try:
            file = consumer_file(db, row)
            requested = ReadDocument(file_id=file.id, selector=row.binding["selector"])
            selector = missing_scope(db, file, current_index(db, file), requested.selector)
            if not selector:
                row.status, row.claim_token, row.lease_until = "ready", None, None
                wake(db, row)
                db.commit()
                return operations.receipt(row)
            if row.executor_started_at:
                # The old parser may have executed. Never silently launch the
                # same operation again after an ambiguous worker crash.
                if row.attempt_token != x_run_attempt:
                    fail(db, row, "document_dispatch_uncertain")
                    db.commit()
                    return operations.receipt(row)
                raise HTTPException(409, "document_operation_already_dispatched")
            operations.claim(db, row)
            if row.status != "running":
                db.commit()
                return operations.receipt(row)
            row.attempt_token, row.executor_started_at = x_run_attempt, datetime.now(UTC)
            row.result = {"executing_selector": selector}
            file.meta = {
                **file.meta,
                "document_job_id": x_run_attempt,
                "document_profile": "scope",
                "document_selector": selector,
                "document_kind": (file.meta.get("extract") or {})["kind"],
                "document_operation_id": row.id,
                "document_deadline": operations.aware(row.execution_deadline).isoformat(),
            }
            db.commit()
            return {
                **operations.receipt(row),
                "job_id": x_run_attempt,
                "version": file.sha256,
                "filename": file.filename,
                "kind": file.meta["document_kind"],
                "content_type": file.content_type,
                "size": file.size,
            }
        except HTTPException as exc:
            fail(db, row, str(exc.detail))
            db.commit()
            return operations.receipt(row)


@router.post("/{file_id}/native/scope/complete")
def complete(file_id: str, body: Complete, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    row = claimed(db, file_id, body.job_id, x_run_attempt)
    owner = row.user_id
    fingerprint = hashlib.sha256(((x_run_attempt or "") + body.model_dump_json()).encode()).hexdigest()
    if row.status in {"ready", "failed"} and row.result.get("completion_fingerprint") == fingerprint:
        return operations.receipt(row)
    manifest = None
    if not body.reason:
        prefix = f"derived/{file_id}/office/{row.id}-{x_run_attempt}/"
        if (
            body.manifest_key != prefix + "manifest.json"
            or {r.key for r in body.receipts} != {prefix + "manifest.json", prefix + "text.txt"}
            or len(body.receipts) != 2
        ):
            raise HTTPException(422, "invalid_native_completion")
        db.rollback()
        storage = get_storage()
        if storage.size(body.manifest_key) > 4 * 1024**2:
            raise HTTPException(413, "native_structure_limit")
        try:
            manifest = json.loads(storage.get_bytes(body.manifest_key))
        except (ValueError, FileNotFoundError):
            raise HTTPException(422, "invalid_native_manifest") from None
    with user_critical_section(db, owner):
        row = claimed(db, file_id, body.job_id, x_run_attempt)
        if row.status in {"ready", "failed"} and row.result.get("completion_fingerprint") == fingerprint:
            return operations.receipt(row)
        operations.active(db, row.id, row.claim_token)
        file = consumer_file(db, row)
        if body.reason:
            if body.manifest_key or body.receipts:
                raise HTTPException(422, "invalid_native_completion")
            fail(db, row, body.reason)
        else:
            selector = row.result["executing_selector"]
            if (
                not isinstance(manifest, dict)
                or manifest.get("parser") != PARSER_NATIVE
                or manifest.get("version") != 2
                or manifest.get("scope") != selector
                or manifest.get("kind") != (file.meta.get("extract") or {}).get("kind")
                or any(
                    type(manifest.get(field)) is not int or not 0 <= manifest[field] <= maximum
                    for field, maximum in (("source_chars", 200000), ("source_blocks", 10000))
                )
                or (
                    selector["kind"] == "pages"
                    and {unit.get("unit_number") for unit in manifest.get("units", [])} != set(selector["pages"])
                )
                or manifest.get("chars", 200001) > 200000
                or len(manifest.get("units", [])) > 10000
                or (manifest.get("cells") or 0) > 200000
            ):
                raise HTTPException(422, "invalid_native_manifest")
            try:
                publish(db, row, file, manifest, body)
            except HTTPException as exc:
                # Publication errors roll back every index/asset/budget change.
                db.rollback()
                row = claimed(db, file_id, body.job_id, x_run_attempt)
                fail(db, row, str(exc.detail))
        row.result = {**row.result, "completion_fingerprint": fingerprint}
        db.commit()
        return operations.receipt(row)


def publish(db, row, file, manifest, body):
    selector = row.result["executing_selector"]
    source = file.meta.get("extract") or {}
    base_id = current_index(db, file).id
    previous = file.meta.get("native_range_budget") or {}
    previous = (
        previous
        if previous.get("base_derivation_id") == base_id
        else {
            "chars": source.get("chars", 0),
            "blocks": source.get("source_blocks", 0),
            "next_ordinal": 0,
        }
    )
    if manifest["kind"] == "pdf":
        updated = {**previous, "chars": previous["chars"] + manifest["chars"], "blocks": previous["blocks"] + len(manifest["units"])}
        if updated["chars"] > 200000 or updated["blocks"] > 10000:
            raise HTTPException(413, "native_structure_limit")
    else:
        updated = {**previous, "chars": manifest["source_chars"], "blocks": manifest["source_blocks"]}
    for receipt in body.receipts:
        asset = assets.publish(db, receipt.key, receipt.token)
        if asset.job_id != row.id or asset.attempt_token != row.attempt_token or asset.kind != "native":
            raise HTTPException(409, "native_scope_superseded")
    derivation = index_document(db, file, body.manifest_key, manifest=manifest, kind="native-scope")
    scopes = [{"kind": "pages", "pages": [page]} for page in selector["pages"]] if selector["kind"] == "pages" else [selector]
    for ordinal, scope in enumerate(scopes, previous["next_ordinal"]):
        db.add(
            DocumentNativeRange(
                file_id=file.id,
                source_version=file.sha256,
                parser=PARSER_NATIVE,
                selector_key=scope_key(scope),
                base_derivation_id=base_id,
                selector=scope,
                ordinal=ordinal,
                page=scope["pages"][0] if scope["kind"] == "pages" else None,
                derivation_id=derivation.id,
            )
        )
    file.meta = {
        **file.meta,
        "native_range_budget": {**updated, "base_derivation_id": base_id, "next_ordinal": previous["next_ordinal"] + len(scopes)},
        "native_range_revision": int(file.meta.get("native_range_revision", 0)) + 1,
    }
    db.flush()
    row.status, row.claim_token, row.lease_until = "ready", None, None
    row.result = {"selector": selector, "derivation_id": derivation.id}
    wake(db, row)
