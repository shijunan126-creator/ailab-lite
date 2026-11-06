"""Worker-driven explicit OCR operations; progress, publication and wakeups are durable."""

import asyncio
import hashlib
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..contracts.workspace_ocr import NormalizedImage
from ..db import SessionLocal
from ..deps import get_db
from ..locks import user_critical_section
from ..models import DocumentOcrUnit, DocumentOperation, OutboxMessage, RenderedPage
from ..service_auth import require_scope
from ..storage import get_storage
from . import derived_assets as assets
from . import document_operations as operations
from .document_ocr import cached, consumer_file, scope_key, visual_input
from .routes_workspace_ocr import infer_while_owned

log = logging.getLogger("ailab.api.document_ocr")
router = APIRouter(prefix="/internal/document-operations", dependencies=[Depends(require_scope("files:ocr"))])


def claimed(db, operation_id, attempt):
    row = db.get(DocumentOperation, operation_id, populate_existing=True)
    job = db.get(OutboxMessage, row.job_id, populate_existing=True) if row and row.job_id else None
    if (
        not row
        or row.kind != "chat_ocr"
        or not job
        or job.id != row.id
        or not attempt
        or job.claim_token != attempt
        or not job.claimed_until
        or operations.aware(job.claimed_until) <= datetime.now(UTC)
    ):
        raise HTTPException(409, "document_job_superseded")
    return row


def wake(db, row):
    from ..models import FileObject
    from .file_dependencies import wake_file

    file = db.get(FileObject, row.file_id)
    if file:
        wake_file(db, file)


def release_inputs(db, row):
    from ..models import DerivedAssetRef

    refs = db.scalars(
        select(DerivedAssetRef.id)
        .where(DerivedAssetRef.id.startswith("document-input:" + row.id + ":"), DerivedAssetRef.kind == "transfer")
        .limit(20)
    ).all()
    for identity in refs:
        assets.unpin(db, identity)


def fail(db, row, reason):
    release_inputs(db, row)
    row.status, row.claim_token, row.lease_until = "failed", None, None
    row.result = {"reason": reason, "completed_scopes": (row.result or {}).get("completed_scopes", [])}
    wake(db, row)


@router.post("/{operation_id}/step")
def step(operation_id: str, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    row = claimed(db, operation_id, x_run_attempt)
    with user_critical_section(db, row.user_id):
        row = claimed(db, operation_id, x_run_attempt)
        if row.status in {"ready", "failed", "canceled"}:
            return operations.receipt(row)
        if row.status == "canceling":
            operations.claim(db, row)  # expire only after the owned executor/reap deadline
            if row.status in {"failed", "canceled"}:
                release_inputs(db, row)
                wake(db, row)
            db.commit()
            return operations.receipt(row)
        try:
            file = consumer_file(db, row)
            if file.status != "ready" or (file.meta or {}).get("scan", {}).get("verdict") != "clean":
                raise HTTPException(409, "document_source_unavailable")
            selectors = row.binding["selectors"]
            existing = cached(db, file, selectors)
            if all(unit is not None for unit in existing):
                row.status, row.claim_token, row.lease_until = "ready", None, None
                row.result = {"completed_scopes": selectors}
                release_inputs(db, row)
                wake(db, row)
                db.commit()
                return operations.receipt(row)
            # Aggregate the render demand before recording its first job, so a
            # 20-page OCR request does not schedule 20 serial conversions.
            if not file.content_type.startswith("image/"):
                wanted = sorted(
                    {
                        scope["pages"][0] if scope["kind"] == "pages" else scope.get("page")
                        for scope, unit in zip(selectors, existing, strict=True)
                        if unit is None and not scope.get("image_id")
                    }
                )
                wanted = [page for page in wanted if page is not None]
                present = (
                    set(
                        db.scalars(
                            select(RenderedPage.page).where(
                                RenderedPage.file_id == file.id, RenderedPage.version == "poppler-1280-v1", RenderedPage.page.in_(wanted)
                            )
                        )
                    )
                    if wanted
                    else set()
                )
                missing = [page for page in wanted if page not in present]
                if missing:
                    from .routes_ocr import request_page_render

                    meta = file.meta or {}
                    known_total = (meta.get("ocr_preview") or {}).get("page_count") or meta.get("render_page_count")
                    if type(known_total) is int and any(page > known_total for page in missing):
                        raise HTTPException(422, "document_page_out_of_range")
                    previous_render = (row.result or {}).get("render_job_id")
                    if (
                        previous_render
                        and meta.get("preview_job_id") == previous_render
                        and (meta.get("preview") or {}).get("status") == "failed"
                    ):
                        raise HTTPException(422, "document_page_images_failed")
                    operations.budget(operations.live_consumer(db, row))
                    request_page_render(db, file, missing, run=operations.live_consumer(db, row))
                    render_id = (file.meta or {}).get("preview_job_id")
                    row.result = {**(row.result or {}), "render_job_id": render_id}
                    render_job = db.get(OutboxMessage, render_id) if render_id else None
                    if render_job:
                        current_deadline = datetime.fromisoformat(render_job.payload["deadline"])
                        render_job.payload = {
                            **render_job.payload,
                            "deadline": min(current_deadline, operations.aware(row.deadline)).isoformat(),
                        }
                    db.commit()
                    return {**operations.receipt(row), "waiting_for": "page-images"}
            images = []
            for selector, unit in zip(selectors, existing, strict=True):
                if unit is not None:
                    continue
                image = visual_input(db, row, file, selector)
                if image is None:
                    db.commit()
                    return {**operations.receipt(row), "waiting_for": "page-images"}
                images.append({**image, "selector": selector})
            if row.executor_started_at and row.attempt_token != x_run_attempt:
                raise HTTPException(409, "document_dispatch_uncertain")
            for image in images:
                identity = "document-input:" + row.id + ":" + scope_key(image["selector"])[:24]
                assets.pin(db, image["key"], identity, kind="transfer", expires_at=operations.aware(row.deadline))
            work_pages = ["page:" + str(image["page"]) if image["page"] else "image:" + image["selector"]["image_id"] for image in images]
            row.result = {**(row.result or {}), "work_pages": work_pages}
            operations.claim(db, row)
            if row.status == "running":
                row.attempt_token = x_run_attempt
                row.result = {**(row.result or {}), "images": images}
                db.commit()
                return {**operations.receipt(row), "image": images[0], "image_index": 0}
            if row.status in {"ready", "failed", "canceled"}:
                release_inputs(db, row)
                wake(db, row)
            db.commit()
            return operations.receipt(row)
        except HTTPException as exc:
            if row.status not in {"ready", "failed", "canceled"}:
                fail(db, row, exc.detail if isinstance(exc.detail, str) else "ocr_preparation_failed")
                db.commit()
            return operations.receipt(row)


def execute_image(db, operation_id, attempt, token, ordinal, receipt, data):
    row = claimed(db, operation_id, attempt)
    owner = row.user_id
    storage = get_storage()
    grant, succeeded = None, False
    try:
        with user_critical_section(db, owner):
            row = claimed(db, operation_id, attempt)
            operations.active(db, row.id, token)
            file = consumer_file(db, row)
            images = (row.result or {}).get("images", [])
            if not 0 <= ordinal < len(images) or hashlib.sha256(data).hexdigest() != receipt.image_sha256:
                raise HTTPException(409, "ocr_image_binding_changed")
            image = images[ordinal]
            selector = image["selector"]
            if cached(db, file, [selector])[0] is not None:
                return operations.receipt(row)
            if row.executor_started_at is not None:
                raise HTTPException(409, "document_operation_already_dispatched")
            row.executor_started_at, row.attempt_token = datetime.now(UTC), attempt
            row.result = {**row.result, "dispatched_index": ordinal}
            key = f"derived/{file.id}/ocr-scopes/{row.id}-{row.generation}-{scope_key(selector)[:24]}.txt"
            assets.unpin(db, "document-input:" + row.id + ":" + scope_key(selector)[:24])
            reconstructible = image["source"] != "browser"
            grant = assets.reserve(db, key, 800000, file.sha256, kind="ocr", reconstructible=reconstructible)
            deadline = operations.aware(row.execution_deadline)
            db.commit()
        # The operation's fixed batch deadline covers every page and crop, not
        # one fresh timeout for every repeated HTTP call.
        result = asyncio.run(infer_while_owned(data, row.id, token, deadline))
        body = result["text"].encode("utf-8")
        storage.put_bytes(key, body, "text/plain; charset=utf-8")
        with user_critical_section(db, owner):
            row = claimed(db, operation_id, attempt)
            operations.active(db, row.id, token)
            file = consumer_file(db, row)
            if (row.result or {}).get("dispatched_index") != ordinal:
                raise HTTPException(409, "document_operation_superseded")
            assets.finish(db, key, grant["token"], len(body), hashlib.sha256(body).hexdigest())
            counter = int((file.meta or {}).get("ocr_next_ordinal", 0))
            unit = DocumentOcrUnit(
                file_id=file.id,
                source_version=file.sha256,
                fingerprint=settings.ocr_engine_fingerprint,
                selector_key=scope_key(selector),
                selector=selector,
                details=receipt.model_dump(mode="json", exclude={"image_sha256"}),
                ordinal=counter,
                page=image["page"],
                image_revision=image["revision"],
                source_kind=image["source"],
                text_key=key,
                digest=hashlib.sha256(body).hexdigest(),
                chars=len(result["text"]),
                engine=result["engine"],
                backend=result["backend"],
                seconds=result.get("seconds", 0),
                operation_id=row.id,
            )
            db.add(unit)
            db.flush()
            if not reconstructible:
                assets.pin(db, key, "ocr-unit:" + unit.id, kind="file")
            file.meta = {
                **(file.meta or {}),
                "ocr_next_ordinal": counter + 1,
                "ocr_cache_revision": int((file.meta or {}).get("ocr_cache_revision", 0)) + 1,
            }
            completed = [*(row.result or {}).get("completed_scopes", []), selector]
            row.executor_started_at = None
            row.result = {"completed_scopes": completed}
            db.flush()
            if all(unit is not None for unit in cached(db, file, row.binding["selectors"])):
                row.status, row.claim_token, row.lease_until = "ready", None, None
                release_inputs(db, row)
                wake(db, row)
            db.commit()
            succeeded = True
            return operations.receipt(row)
    except Exception as exc:
        db.rollback()
        with user_critical_section(db, owner):
            row = db.get(DocumentOperation, operation_id, populate_existing=True)
            if row and row.claim_token == token and row.status in {"running", "canceling"}:
                from ..ocr_backend import OCRExecutionUncertain

                if isinstance(exc, OCRExecutionUncertain) or (row.status == "canceling" and row.executor_started_at):
                    row.status = "canceling"
                    row.result = {**row.result, "reason": "document_dispatch_uncertain"}
                else:
                    fail(db, row, "document_operation_deadline" if isinstance(exc, TimeoutError) else "ocr_failed")
                db.commit()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, "document_ocr_failed") from None
    finally:
        if grant and not succeeded:
            db.rollback()
            from ..models import DerivedAsset

            with user_critical_section(db, owner):
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
                    log.warning("document OCR cleanup pending operation=%s", operation_id)


@router.post("/{operation_id}/image")
async def image(
    operation_id: str,
    request: Request,
    image_index: int = Query(ge=0, le=19),
    x_document_token: str = Header(min_length=1, max_length=40),
    x_image_receipt: str = Header(min_length=1, max_length=4096),
    x_run_attempt: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    try:
        receipt = NormalizedImage.model_validate_json(x_image_receipt)
    except ValueError:
        raise HTTPException(422, "invalid_ocr_image_receipt") from None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > settings.ocr_page_max_bytes:
            raise HTTPException(413, "image_size_limit")
        body.extend(chunk)
    data = bytes(body)
    body.clear()
    from .owned_execution import execute_owned

    def abandon_request():
        with SessionLocal() as owned_db:
            row = owned_db.get(DocumentOperation, operation_id, populate_existing=True)
            if row:
                with user_critical_section(owned_db, row.user_id):
                    operations.lane_lock(owned_db, row.lane)
                    owned_db.refresh(row)
                    if row.claim_token == x_document_token and row.status == "running":
                        row.status = "canceling" if row.executor_started_at else "canceled"
                        owned_db.commit()

    return await execute_owned(
        request, lambda: execute_image(db, operation_id, x_run_attempt, x_document_token, image_index, receipt, data), abandon_request
    )


@router.post("/{operation_id}/fail")
def worker_failure(operation_id: str, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    row = claimed(db, operation_id, x_run_attempt)
    with user_critical_section(db, row.user_id):
        row = claimed(db, operation_id, x_run_attempt)
        if row.status in {"pending", "running"} and row.executor_started_at is None:
            fail(db, row, "ocr_image_preparation_failed")
            db.commit()
        return operations.receipt(row)
