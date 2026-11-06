"""Work image OCR: Broker supplies bounded pixels; API owns permission, slots and credentials."""

import asyncio
import hashlib
import hmac
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..contracts.workspace_ocr import PreparedImage
from ..db import SessionLocal
from ..deps import get_db
from ..domain.document_reading import CursorError, Position, decode_cursor, encode_cursor
from ..domain.document_tools import catalog_version
from ..locks import user_critical_section
from ..models import DocumentOperation, RunStatus
from ..ocr_backend import recognize
from ..service_auth import require_scope
from ..storage import get_storage
from . import derived_assets as assets
from . import document_operations as operations
from .routes_internal import _run_or_404
from .runtime_fence import fenced_run

log = logging.getLogger("ailab.api.workspace_ocr")
router = APIRouter(prefix="/internal/runs", dependencies=[Depends(require_scope("ocr:workspace"))])


def admit(run):
    if (run.options or {}).get("mode", "work") != "work" or catalog_version(run.options) != 2:
        raise HTTPException(422, "workspace_ocr_not_available")
    if run.status != RunStatus.RUNNING:
        raise HTTPException(409, "run_unavailable")


@router.post("/{run_id}/workspace-ocr/budget")
def start_budget(run_id: str, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    run = _run_or_404(db, run_id)
    with fenced_run(db, run, x_run_attempt):
        admit(run)
        deadline = operations.budget(run, start=True)
        db.commit()
        return {"deadline": deadline.isoformat()}


@router.post("/{run_id}/workspace-ocr")
def prepare(run_id: str, body: PreparedImage, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    from ..paths import normalize_path

    if body.width * body.height > 16_000_000:
        raise HTTPException(413, "image_pixel_limit")
    try:
        path = normalize_path(body.path)
    except ValueError:
        raise HTTPException(422, "invalid_workspace_path") from None
    if path.startswith("/workspace/.ailab/"):
        raise HTTPException(422, "invalid_workspace_path")
    run = _run_or_404(db, run_id)
    with fenced_run(db, run, x_run_attempt):
        admit(run)
        if not settings.ocr_executor_url:
            raise HTTPException(503, "ocr_executor_unavailable")
        binding = {**body.model_dump(mode="json"), "path": path, "engine": settings.ocr_engine_fingerprint}
        row = operations.request(db, run, lane="ocr", kind="workspace_ocr", version=body.image_sha256, binding=binding)
        db.commit()
    with user_critical_section(db, run.user_id):
        operations.claim(db, row)
        db.commit()
        return operations.receipt(row)


@router.get("/{run_id}/workspace-ocr/{operation_id}")
def status(
    run_id: str,
    operation_id: str,
    cursor: str | None = None,
    x_run_attempt: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    run = _run_or_404(db, run_id)
    with fenced_run(db, run, x_run_attempt):
        admit(run)
        row = db.get(DocumentOperation, operation_id, populate_existing=True)
        if not row or row.run_id != run.id or row.kind != "workspace_ocr":
            raise HTTPException(404, "document_operation_unavailable")
        operations.claim(db, row)
        result = operations.receipt(row)
        if row.status == "ready":
            result["content"] = read_result(db, row, cursor)
        db.commit()
        return result


def read_result(db, row, cursor=None):
    key = row.result.get("text_key")
    storage = get_storage()
    data = bytearray()
    with storage.open_stream(key) as chunks:
        for chunk in chunks:
            if len(data) + len(chunk) > 800000:
                raise HTTPException(409, "ocr_cache_changed")
            data.extend(chunk)
    if hashlib.sha256(data).hexdigest() != row.result.get("digest"):
        raise HTTPException(409, "ocr_cache_changed")
    text = data.decode("utf-8")
    signing_key = hmac.digest(settings.caller_token("api").encode(), b"workspace-ocr-cursor", "sha256")
    context = hashlib.sha256((row.id + row.source_version + row.result["digest"]).encode()).hexdigest()
    try:
        position = decode_cursor(cursor, context, key=signing_key)
        if position.ordinal != 0 or position.offset > len(text):
            raise CursorError("invalid_cursor")
    except CursorError as exc:
        raise HTTPException(409, str(exc)) from None
    end = min(len(text), position.offset + 8000)
    return {
        "text": text[position.offset : end],
        "text_source": "ocr",
        "source": row.binding,
        "next_cursor": encode_cursor(context, Position(0, end), key=signing_key) if end < len(text) else None,
        "truncated": end < len(text),
        "operation_id": row.id,
        "engine": row.result["engine"],
        "coverage": {"start": position.offset, "end": end, "total_chars": len(text), "visual": "not_viewed"},
    }


async def infer_while_owned(data, operation_id, token, deadline):
    def owned():
        with SessionLocal() as db:
            try:
                operations.active(db, operation_id, token)
                return True
            except HTTPException:
                return False

    task = asyncio.create_task(recognize(data, operation_id=operation_id, deadline=deadline.timestamp()))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.25)
            if not await asyncio.to_thread(owned):
                raise ValueError("document_operation_superseded")
        return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def execute(db, run_id, operation_id, token, attempt, data):
    run = _run_or_404(db, run_id)
    grant = None
    succeeded = False
    storage = get_storage()
    try:
        with fenced_run(db, run, attempt):
            admit(run)
            row = operations.active(db, operation_id, token, run_id=run_id)
            if row.kind != "workspace_ocr" or hashlib.sha256(data).hexdigest() != row.source_version:
                raise HTTPException(409, "ocr_image_binding_changed")
            if row.executor_started_at is not None:
                raise HTTPException(409, "document_operation_already_dispatched")
            row.executor_started_at, row.attempt_token = datetime.now(UTC), attempt
            deadline = operations.aware(row.execution_deadline)
            key = f"viewer/{run.id}/ocr/{row.id}-{row.generation}.txt"
            grant = assets.reserve(db, key, 800000, row.source_version, kind="ocr", reconstructible=False)
            db.commit()
        result = asyncio.run(infer_while_owned(data, row.id, token, deadline))
        body = result["text"].encode("utf-8")
        # Reserve was committed before either inference or the first stored byte.
        storage.put_bytes(key, body, "text/plain; charset=utf-8")
        with user_critical_section(db, run.user_id):
            row = operations.active(db, operation_id, token, run_id=run_id)
            assets.finish(db, key, grant["token"], len(body), hashlib.sha256(body).hexdigest())
            assets.pin(db, key, "workspace-ocr:" + row.id, kind="message", run_id=run.id)
            row.status, row.claim_token, row.lease_until = "ready", None, None
            row.result = {
                "text_key": key,
                "digest": hashlib.sha256(body).hexdigest(),
                "chars": len(result["text"]),
                "engine": result["engine"],
                "backend": result["backend"],
                "seconds": result.get("seconds", 0),
            }
            db.commit()
            succeeded = True
            return {**operations.receipt(row), "content": read_result(db, row)}
    except Exception as exc:
        db.rollback()
        with user_critical_section(db, run.user_id):
            row = db.get(DocumentOperation, operation_id, populate_existing=True)
            if row and row.claim_token == token and row.status in {"running", "canceling"}:
                row.status = "canceled" if not operations.source_available(db, row, active=True) else "failed"
                row.claim_token, row.lease_until = None, None
                row.result = {"reason": "document_operation_deadline" if isinstance(exc, TimeoutError) else "ocr_failed"}
                db.commit()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, "workspace_ocr_failed") from None
    finally:
        if grant and not succeeded:
            # If publish committed but the response was lost, never delete a
            # canonical asset. All cleanup uses the still-unpublished grant.
            db.rollback()
            with user_critical_section(db, run.user_id):
                from ..models import DerivedAsset

                asset = db.get(DerivedAsset, grant["key"], populate_existing=True)
                deletable = bool(asset and asset.token == grant["token"] and asset.state in {"writing", "staged", "deleting"})
                if deletable:
                    assets.abandon(db, grant["key"], grant["token"])
                    db.commit()
            if deletable:
                try:
                    storage.delete(grant["key"])
                    assets.forget_deleted(db, grant["key"])
                    db.commit()
                except Exception:
                    db.rollback()
                    log.warning("workspace OCR cleanup pending operation=%s", operation_id)


@router.post("/{run_id}/workspace-ocr/{operation_id}/image")
async def image(
    run_id: str,
    operation_id: str,
    request: Request,
    x_document_token: str = Header(min_length=1, max_length=40),
    x_run_attempt: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > settings.ocr_page_max_bytes:
            raise HTTPException(413, "image_size_limit")
        payload.extend(chunk)
    if not payload:
        raise HTTPException(422, "image_required")
    data = bytes(payload)
    payload.clear()

    from .owned_execution import execute_owned

    def abandon_request():
        with SessionLocal() as owned_db:
            cancel(run_id, operation_id, x_document_token=x_document_token, db=owned_db)

    return await execute_owned(request, lambda: execute(db, run_id, operation_id, x_document_token, x_run_attempt, data), abandon_request)


@router.post("/{run_id}/workspace-ocr/{operation_id}/cancel")
def cancel(run_id: str, operation_id: str, x_document_token: str = Header(min_length=1, max_length=40), db: Session = Depends(get_db)):
    row = db.get(DocumentOperation, operation_id, populate_existing=True)
    if not row or row.run_id != run_id or row.kind != "workspace_ocr":
        raise HTTPException(404, "document_operation_unavailable")
    with user_critical_section(db, row.user_id):
        operations.lane_lock(db, row.lane)
        db.refresh(row)
        if row.claim_token == x_document_token and row.status in {"running", "canceling"}:
            row.status = "canceling" if row.executor_started_at else "canceled"
            db.commit()
        return operations.receipt(row)
