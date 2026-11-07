"""Durable Worker orchestration and bounded image publication for Chat views."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..contracts.document_input import ViewDocument
from ..contracts.workspace_ocr import NormalizedImage
from ..deps import get_db
from ..locks import user_critical_section
from ..models import DocumentOperation, OutboxMessage, Run, RunStatus
from ..service_auth import require_scope
from . import document_operations as operations
from . import document_visual as visual
from .document_ocr import consumer_file
from .native_ranges import charge_consumption
from .routes_library_tools import authorized_source
from .runtime_fence import fenced_run

router = APIRouter()


@router.post("/internal/runs/{run_id}/documents/view_document", dependencies=[Depends(require_scope("files:read_for_run"))])
def view_document(run_id: str, body: ViewDocument, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    from ..domain.document_tools import catalog_version

    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run_unavailable")
    if catalog_version(run.options) != 2 or (run.options or {}).get("mode") != "chat":
        raise HTTPException(422, "document_tool_not_available_for_catalog")
    from ..catalog import get_catalog

    spec = get_catalog().get(run.model)
    if not spec or not spec.supports({"vision"}):
        raise HTTPException(422, "model_vision_required")
    with fenced_run(db, run, x_run_attempt):
        if run.status != RunStatus.RUNNING:
            raise HTTPException(409, "run_unavailable")
        file = authorized_source(db, run, body.file_id)
        charge_consumption(db, run, file)
        result = visual.requested(db, run, file, body)
        db.commit()
        return result


def claimed(db, operation_id, attempt):
    row = db.get(DocumentOperation, operation_id, populate_existing=True)
    job = db.get(OutboxMessage, row.job_id, populate_existing=True) if row and row.job_id else None
    if (
        not row
        or row.kind != "chat_view"
        or not job
        or job.id != row.id
        or not attempt
        or job.claim_token != attempt
        or not job.claimed_until
        or operations.aware(job.claimed_until) <= datetime.now(UTC)
    ):
        raise HTTPException(409, "document_job_superseded")
    return row


@router.post("/internal/document-views/{operation_id}/step", dependencies=[Depends(require_scope("files:preview_write"))])
def step(operation_id: str, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    row = claimed(db, operation_id, x_run_attempt)
    with user_critical_section(db, row.user_id):
        row = claimed(db, operation_id, x_run_attempt)
        if row.status in visual.TERMINAL:
            return operations.receipt(row)
        try:
            file = consumer_file(db, row)
            if operations.aware(row.deadline) <= datetime.now(UTC):
                raise HTTPException(408, "document_run_deadline")
            if row.status == "browser":
                visual.browser_step(db, row, file)
            if row.status == "preparing":
                images = visual.server_inputs(db, row, file)
                if images is None:
                    db.commit()
                    return {**operations.receipt(row), "waiting_for": "page-images"}
                if row.binding["target"]["kind"] in {"pages", "sheet"}:
                    # Full server pages are already canonical; do not encode or
                    # store a second model-only copy of the same page pixels.
                    row.result = {
                        **row.result,
                        "files": [
                            {
                                "id": file.id,
                                "file_id": file.id,
                                "filename": file.filename,
                                "page": item["page"],
                                "content_type": "image/jpeg",
                                "source": item["source"],
                                "version": file.sha256,
                                "image_revision": item["revision"],
                                "representation_revision": item["revision"],
                                "location_verified": True,
                                "pixels_verified": True,
                            }
                            for item in images
                        ],
                    }
                    row.status = "ready"
                    visual.wake(db, row)
                    db.commit()
                    return operations.receipt(row)
                from .derived_assets import pin

                for index, item in enumerate(images):
                    pin(
                        db, item["key"], f"document-view-input:{row.id}:{index}", kind="transfer", expires_at=operations.aware(row.deadline)
                    )
                row.result = {**row.result, "images": images}
                row.status = "pending"
            if row.status == "pending":
                operations.claim(db, row)
                if row.status == "running":
                    row.attempt_token = x_run_attempt
            if row.status == "running":
                db.flush()
                operations.active(db, row.id, row.claim_token)
                if row.attempt_token != x_run_attempt:
                    raise HTTPException(409, "document_dispatch_uncertain")
                index = len(row.result.get("files", []))
                images = row.result["images"]
                if index >= len(images):
                    row.status, row.claim_token, row.lease_until = "ready", None, None
                    visual.wake(db, row)
                else:
                    db.commit()
                    return {**operations.receipt(row), "image": images[index], "image_index": index}
            db.commit()
            return operations.receipt(row)
        except HTTPException as exc:
            visual.fail(db, row, str(exc.detail))
            db.commit()
            return operations.receipt(row)


@router.post("/internal/document-views/{operation_id}/image", dependencies=[Depends(require_scope("files:preview_write"))])
async def image(
    operation_id: str,
    request: Request,
    image_index: int = Query(ge=0, le=3),
    x_document_token: str = Header(min_length=1, max_length=40),
    x_image_receipt: str = Header(min_length=1, max_length=4096),
    x_run_attempt: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    import hashlib

    from fastapi.concurrency import run_in_threadpool

    from .viewer_captures import MAX_IMAGE, save

    try:
        receipt = NormalizedImage.model_validate_json(x_image_receipt)
    except ValueError:
        raise HTTPException(422, "invalid_image_receipt") from None
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_IMAGE:
            raise HTTPException(413, "image_size_limit")
        payload.extend(chunk)
    if hashlib.sha256(payload).hexdigest() != receipt.image_sha256 or receipt.width * receipt.height > 16_000_000:
        raise HTTPException(422, "invalid_image_receipt")
    row = claimed(db, operation_id, x_run_attempt)
    owner, run_id = row.user_id, row.run_id
    generation = row.generation

    def validate():
        current = claimed(db, operation_id, x_run_attempt)
        operations.active(db, operation_id, x_document_token)
        if current.generation != generation or image_index != len(current.result.get("files", [])):
            raise HTTPException(409, "document_view_generation_changed")

    def publish():
        with user_critical_section(db, owner):
            validate()
            row = db.get(DocumentOperation, operation_id)
            item = row.result["images"][image_index]
            target = {
                "file_id": row.file_id,
                "version": row.source_version,
                "source": item["source"],
                "page": item["page"],
                "selector": row.binding["target"],
                "image_revision": item["revision"],
                "region": receipt.actual_region.model_dump(mode="json") if receipt.actual_region else None,
            }

            def bind(saved):
                current = db.get(DocumentOperation, operation_id)
                current.result = {
                    **current.result,
                    "files": [
                        *current.result.get("files", []),
                        {
                            **saved,
                            "file_id": current.file_id,
                            "filename": "文档页图",
                            "page": item["page"],
                            "content_type": "image/jpeg",
                            "source": item["source"],
                            "version": current.source_version,
                            "image_revision": receipt.image_sha256,
                            "representation_revision": item["revision"],
                            "actual_region": receipt.actual_region.model_dump(mode="json") if receipt.actual_region else None,
                            "width": receipt.width,
                            "height": receipt.height,
                            "location_verified": True,
                            "pixels_verified": item["source"] not in {"browser", "viewport"},
                        },
                    ],
                }
                if len(current.result["files"]) == len(current.result["images"]):
                    current.status, current.claim_token, current.lease_until = "ready", None, None
                    from .derived_assets import unpin

                    for index in range(len(current.result["images"])):
                        unpin(db, f"document-view-input:{current.id}:{index}")
                    visual.wake(db, current)

            save(
                db,
                db.get(Run, run_id),
                f"{operation_id}:{generation}:{image_index}",
                target,
                payload,
                "image/jpeg",
                validate=validate,
                bind=bind,
            )
            return operations.receipt(db.get(DocumentOperation, operation_id))

    try:
        return await run_in_threadpool(publish)
    finally:
        payload.clear()


@router.post("/internal/document-views/{operation_id}/fail", dependencies=[Depends(require_scope("files:preview_write"))])
def failed(operation_id: str, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    row = claimed(db, operation_id, x_run_attempt)
    with user_critical_section(db, row.user_id):
        row = claimed(db, operation_id, x_run_attempt)
        if row.status not in visual.TERMINAL:
            visual.fail(db, row, "document_image_preparation_failed")
            db.commit()
        return operations.receipt(row)
