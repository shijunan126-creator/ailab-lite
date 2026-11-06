"""Explicit file-bound RapidOCR jobs. API owns unbilled page/engine/time records; Worker drives durable progress."""

import asyncio
import hashlib
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..deps import current_user, get_db
from ..locks import user_critical_section
from ..models import FileObject, FileStatus, User
from ..service_auth import require_scope
from ..storage import get_storage

# One process-wide slot. A busy API answers 429 and the Worker leaves the job pending for redelivery.
_ocr_slot = threading.BoundedSemaphore(1)
# Only these reasons are shown to clients; anything else is ocr_failed, so upstream bodies never leak.
_EXPLICIT_REASONS = {
    "page_limit",
    "image_size_limit",
    "context_limit",
    "empty_ocr_text",
    "invalid_ocr_response",
    "preview_unavailable",
    "call_already_dispatched",
    "text_limit",
}

router = APIRouter(prefix="/internal/files", dependencies=[Depends(require_scope("files:ocr"))])


def eligible(db, file_id):
    file = db.get(FileObject, file_id, populate_existing=True)
    if not file or file.deleted_at or file.purged_at:
        raise HTTPException(404, "file not found")
    owner = db.get(User, file.user_id, populate_existing=True)
    if not owner or owner.disabled_at:
        raise HTTPException(404, "file not found")
    if file.status != FileStatus.READY or ((file.meta or {}).get("scan") or {}).get("verdict") != "clean":
        raise HTTPException(409, "OCR requires a clean, ready file")
    return file


def request_ocr(db: Session, file: FileObject, *, run=None) -> None:
    """Runs in the READY patch's transaction, so the job exists exactly when OCR is pending."""
    from .jobs import stage_job

    job_id = uuid.uuid4().hex
    file.meta = {**(file.meta or {}), "ocr_requested_at": datetime.now(UTC).timestamp(), "ocr_generation": job_id, "ocr_job_id": job_id}
    # A durable job like any other: claimed, ACKed by the Worker, and only then removed from the outbox.
    job = stage_job(db, "ocr", file_id=file.id, job_id=job_id)
    job.payload = {**job.payload, "deadline": (datetime.now(UTC) + timedelta(seconds=600)).isoformat()}
    from .file_work import register

    file.meta = {**file.meta, "ocr_consumer_run_id": run.id if run else None}
    register(db, file, job, lane="ocr", run=run)


def check_ocr_attempt(db, file, attempt):
    if not attempt:  # Compatibility for pre-claim callers; deployed Workers send their claim token.
        return
    from ..models import OutboxMessage

    job_id = (file.meta or {}).get("ocr_job_id")
    if not job_id:  # Jobs queued before this contract keep their legacy behavior until the next extraction.
        return
    job = db.get(OutboxMessage, job_id)
    if (
        not job
        or job.payload.get("kind") != "ocr"
        or job.payload.get("file_id") != file.id
        or job.claim_token != attempt
        or not job.claimed_until
    ):
        raise HTTPException(409, "stale_ocr_attempt")
    until = job.claimed_until
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    if until <= datetime.now(UTC):
        raise HTTPException(409, "stale_ocr_attempt")


def extraction_pages(meta):
    return meta.get("ocr_selected_pages") or [p + 1 for p in (meta.get("ocr_pdf") or {}).get("low_text_pages", [0])]


def publish_extraction(db, file, selected, records, *, operation=None, attempt=None, new_generation=None):
    import codecs

    from .owned_assets import publish

    file_id, owner, version = file.id, file.user_id, file.sha256
    original_generation = str(file.meta.get("ocr_generation") or file.meta.get("ocr_requested_at"))
    generation = new_generation or file.meta.get("ocr_generation") or uuid.uuid4().hex
    operation_id, token = (operation.id, operation.claim_token) if operation else (None, None)
    snapshot = {str(page - 1): records[str(page - 1)]["text_key"] for page in selected}
    if len(selected) > 20:
        raise ValueError("page_limit")
    db.rollback()
    data, chars = bytearray(), 0
    try:
        storage = get_storage()
        for ordinal, page in enumerate(selected):
            key = snapshot[str(page - 1)]
            if not key.startswith(f"derived/{file_id}/") or ".." in key.split("/"):
                raise ValueError("invalid_ocr_binding")
            prefix = ("\n" if ordinal else "") + f"\f[page:{page}]\n"
            data.extend(prefix.encode())
            chars += len(prefix)
            decoder, size = codecs.getincrementaldecoder("utf-8")("strict"), 0
            with storage.open_stream(key) as chunks:
                for chunk in chunks:
                    size += len(chunk)
                    if size > 800000:
                        raise ValueError("text_limit")
                    chars += len(decoder.decode(chunk))
                    data.extend(chunk)
            chars += len(decoder.decode(b"", final=True))
        target = f"derived/{file_id}/ocr-results/{hashlib.sha256(data).hexdigest()}.txt"

        def validate():
            current = eligible(db, file_id)
            check_ocr_attempt(db, current, attempt)
            if (
                current.sha256 != version
                or str(current.meta.get("ocr_generation") or current.meta.get("ocr_requested_at")) != original_generation
            ):
                raise HTTPException(409, "OCR source changed")
            from .document_sources import ocr_records

            found = ocr_records(db, current, [page - 1 for page in selected])
            if any(found.get(page, {}).get("text_key") != key for page, key in snapshot.items()):
                raise HTTPException(409, "OCR cache changed")
            if operation_id:
                from . import document_operations as operations

                operations.active(db, operation_id, token)

        def bind(text_key):
            current = eligible(db, file_id)
            if operation_id:
                from ..models import DocumentOperation

                row = db.get(DocumentOperation, operation_id)
                row.status, row.claim_token, row.lease_until, row.result = "ready", None, None, {}
            current.meta = {
                **current.meta,
                "ocr_generation": generation,
                "ocr_selected_pages": selected,
                "ocr": "done",
                "ocr_reason": None,
                "ocr_text_key": text_key,
            }
            from .file_dependencies import wake_file

            wake_file(db, current)
            return chars

        return publish(db, owner, target, data, version, kind="ocr", validate=validate, bind=bind, content_type="text/plain; charset=utf-8")
    finally:
        data.clear()


def _total_timeout() -> float:
    return settings.llm_total_timeout_seconds or settings.llm_timeout_seconds


def update(db, file, **fields):
    with user_critical_section(db, file.user_id):
        file = eligible(db, file.id)
        file.meta = {**(file.meta or {}), **fields}
        from .file_dependencies import wake_file

        wake_file(db, file)
        db.commit()
    return fields


@router.post("/{file_id}/ocr/prepare")
def prepare(file_id: str, db: Session = Depends(get_db), attempt: str | None = Header(default=None, alias="X-Run-Attempt")):
    file = db.get(FileObject, file_id)
    if (
        file
        and not file.deleted_at
        and not file.purged_at
        and file.status == FileStatus.READY
        and ((file.meta or {}).get("scan") or {}).get("verdict") != "clean"
    ):
        with user_critical_section(db, file.user_id):
            db.refresh(file)
            check_ocr_attempt(db, file, attempt)
            if ((file.meta or {}).get("scan") or {}).get("verdict") != "clean":
                file.meta = {**(file.meta or {}), "ocr": "failed", "ocr_reason": "scan_required"}
                db.commit()
                return {"ocr": "failed", "reason": "scan_required"}
    file = eligible(db, file_id)
    with user_critical_section(db, file.user_id):
        file = eligible(db, file_id)
        check_ocr_attempt(db, file, attempt)
        meta = dict(file.meta or {})
        now = datetime.now(UTC).timestamp()

        def finish(**fields):
            file.meta = {**meta, **fields}
            from .file_dependencies import wake_file

            wake_file(db, file)
            db.commit()
            return fields

        if meta.get("ocr") in {"pending", "running"} and now - meta.get("ocr_requested_at", now) > settings.ocr_deadline_seconds:
            return finish(ocr="failed", ocr_reason="deadline")
        if meta.get("ocr") == "running":
            if now - meta.get("ocr_started_at", 0) > settings.ocr_executor_timeout_seconds + 30:
                return finish(ocr="pending", ocr_reason="resuming")
            return {"ocr": "running"}
        if meta.get("ocr") != "pending":
            return {"ocr": meta.get("ocr", "unavailable")}
        if now - meta.get("ocr_requested_at", now) > settings.ocr_deadline_seconds:
            return finish(ocr="failed", ocr_reason="deadline")
        if not settings.ocr_executor_url:
            return finish(ocr="unavailable", ocr_reason="ocr_executor_unavailable")
        if meta.get("ocr_pdf") is None and file.size > settings.ocr_max_bytes:
            return finish(ocr="failed", ocr_reason="size_limit")
        pdf = meta.get("ocr_pdf")
        if pdf is None:  # image: the normalized display derivative is the only input the model sees
            if not meta.get("display_key"):
                from .preview_requests import request_preview

                previous = meta.get("preview") or {}
                if previous.get("status") == "failed":
                    return finish(ocr="failed", ocr_reason="normalized_image_unavailable")
                manifest, job_id = request_preview(db, file)
                db.commit()
                if manifest.get("status") == "pending" and job_id is None:
                    job_id = (file.meta or {}).get("preview_job_id")
                if job_id:
                    return {"ocr": "pending", "preview_job_id": job_id}
                return finish(ocr="failed", ocr_reason="normalized_image_unavailable")
            return {"ocr": "pending"}
        if pdf.get("error"):
            return finish(ocr="failed", ocr_reason="pdf_analysis_unavailable")
        if not pdf["low_text_pages"]:
            return finish(ocr="done", ocr_reason="text_pdf")

        preview = meta.get("ocr_preview") or {}
        if preview.get("status") == "failed":
            return finish(ocr="failed", ocr_reason="preview_unavailable")
        if preview.get("status") == "ready":
            return {"ocr": "pending"}
        if meta.get("ocr_render_job_id") and (meta.get("preview") or {}).get("status") == "pending":
            return {"ocr": "pending", "preview_job_id": meta["ocr_render_job_id"]}
        request_page_render(db, file, [p + 1 for p in pdf["low_text_pages"]])
        job_id = file.meta.get("preview_job_id")
        db.commit()
        return {"ocr": "pending", "preview_job_id": job_id}


def bounded_image(key, file_id):
    if not key.startswith(f"derived/{file_id}/") or ".." in key.split("/"):
        raise ValueError("invalid_image_binding")
    storage = get_storage()
    chunks, size = [], 0
    with storage.open_stream(key) as stream:
        for chunk in stream:
            size += len(chunk)
            if size > settings.ocr_page_max_bytes:
                raise ValueError("image_size_limit")
            chunks.append(chunk)
    return b"".join(chunks)


def _reason(exc: Exception) -> str:
    if isinstance(exc, HTTPException):  # quota or ledger rejected the call before any inference
        return "quota_exceeded" if exc.status_code == 429 else "usage_rejected"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ValueError) and str(exc) in _EXPLICIT_REASONS:
        return str(exc)
    return "ocr_failed"


@router.post("/{file_id}/ocr")
def run_ocr(file_id: str, db: Session = Depends(get_db), attempt: str | None = Header(default=None, alias="X-Run-Attempt")):
    if not _ocr_slot.acquire(blocking=False):
        raise HTTPException(429, "OCR busy; retry later")
    try:
        return _run_ocr(file_id, db, attempt)
    finally:
        _ocr_slot.release()


# Conversation image reads use the existing active-run attachment authorization,
# not Worker OCR permission. The only exposed image is a normalized derivative.
vision_router = APIRouter(prefix="/internal/files")


@vision_router.get("/{file_id}/vision")
def vision_image(
    file_id: str,
    run_id: str,
    page: int | None = None,
    principal=Depends(require_scope("files:read_for_run")),
    db: Session = Depends(get_db),
):
    from .routes_internal import _bound_file

    file = _bound_file(db, file_id, principal, run_id, "vision")
    key = (file.meta or {}).get("display_key")
    mime = "image/webp"
    if page is not None:
        from ..models import RenderedPage

        rendered = db.get(RenderedPage, (file.id, page, "poppler-1280-v1"))
        if not rendered:
            raise HTTPException(409, "page image unavailable")
        key, mime = rendered.key, "image/png" if rendered.key.endswith(".png") else "image/jpeg"
    if not key:
        raise HTTPException(409, "normalized image unavailable")
    try:
        return Response(bounded_image(key, file_id), media_type=mime)
    except ValueError:
        raise HTTPException(413, "image size limit") from None


def _run_ocr(file_id: str, db: Session, attempt=None):
    from ..ocr_backend import recognize as remote_recognize

    file = eligible(db, file_id)
    with user_critical_section(db, file.user_id):
        file = eligible(db, file_id)
        check_ocr_attempt(db, file, attempt)
        if not settings.ocr_executor_url:
            return update(db, file, ocr="unavailable", ocr_reason="ocr_executor_unavailable")
        meta = dict(file.meta or {})
        if meta.get("ocr") != "pending":
            return {"ocr": meta.get("ocr", "unavailable")}
        generation = str(meta.get("ocr_generation") or meta.get("ocr_requested_at"))
        requested_at = meta.get("ocr_requested_at", 0)
        targets = []
        if meta.get("ocr_pdf") is None:
            targets = [(0, meta.get("display_key"))]
        else:
            names = (meta.get("ocr_preview") or {}).get("pages") or []
            assets = meta.get("ocr_preview_assets") or {}
            for number in extraction_pages(meta):
                page = number - 1
                name = next((n for n in names if int(n[2:-4]) == page + 1), None)
                targets.append((page, (assets.get(name) or {}).get("key")))
        operation = None
        if attempt and meta.get("ocr_job_id"):
            from ..models import DocumentOperation, OutboxMessage, Run
            from . import document_operations as operations
            from .file_work import register
            from .file_work import start as start_work

            job = db.get(OutboxMessage, meta["ocr_job_id"])
            run = db.get(Run, meta.get("ocr_consumer_run_id")) if meta.get("ocr_consumer_run_id") else None
            operation = db.get(DocumentOperation, job.id, populate_existing=True)
            if operation is None:
                operation = register(db, file, job, lane="ocr", run=run)
            operation.binding = {**operation.binding, "pages": extraction_pages(meta)}
            operation = start_work(db, file, job, attempt)
            if operation.status != "running":
                if operation.status in {"failed", "canceled"}:
                    file.meta = {**file.meta, "ocr": "failed", "ocr_reason": operation.result.get("reason", "ocr_failed")}
                db.commit()
                return {"ocr": "pending" if operation.status == "pending" else "failed"}
        file.meta = {**meta, "ocr": "running", "ocr_reason": None, "ocr_started_at": datetime.now(UTC).timestamp()}
        db.commit()
    try:
        for page, key in targets:
            if datetime.now(UTC).timestamp() - requested_at > settings.ocr_deadline_seconds:
                raise ValueError("deadline")
            file = eligible(db, file_id)
            from .document_sources import ocr_records

            done = ocr_records(db, file, [p for p, _ in targets])
            record = done.get(str(page))
            db.rollback()  # no DB transaction or owner lock across remote inference
            if not record:
                if not key:
                    raise ValueError("preview_unavailable")
                if operation:
                    from .routes_workspace_ocr import infer_while_owned

                    result = asyncio.run(
                        infer_while_owned(
                            bounded_image(key, file_id), operation.id, operation.claim_token, operations.aware(operation.execution_deadline)
                        )
                    )
                else:
                    result = asyncio.run(remote_recognize(bounded_image(key, file_id)))
                from .legacy_ocr_assets import store_page

                store_page(db, file, generation, page, result, attempt=attempt, operation=operation)
                if any(str(p) not in done and p != page for p, _ in targets):
                    with user_critical_section(db, file.user_id):
                        file = eligible(db, file_id)
                        check_ocr_attempt(db, file, attempt)
                        file.meta = {**file.meta, "ocr": "pending"}
                        if operation:
                            operation.executor_started_at = None
                        db.commit()
                    return {"ocr": "pending", "completed_page": page + 1}
        with user_critical_section(db, file.user_id):
            file = eligible(db, file_id)
            if str(file.meta.get("ocr_generation") or file.meta.get("ocr_requested_at")) != generation:
                raise HTTPException(409, "OCR source changed")
            check_ocr_attempt(db, file, attempt)
            if file.meta.get("ocr") != "running":
                return {"ocr": "canceled", "reason": "superseded"}
            chars = publish_extraction(
                db, file, [p + 1 for p, _ in targets], ocr_records(db, file, [p for p, _ in targets]), operation=operation, attempt=attempt
            )
            db.commit()
        return {"ocr": "done", "pages": len(targets), "chars": chars}
    except Exception as exc:
        db.rollback()
        with user_critical_section(db, file.user_id):
            file = eligible(db, file_id)
            check_ocr_attempt(db, file, attempt)
            if str(file.meta.get("ocr_generation") or file.meta.get("ocr_requested_at")) != generation or file.meta.get("ocr") != "running":
                return {"ocr": "canceled", "reason": "superseded"}
            import httpx

            retryable = isinstance(exc, (httpx.TransportError, TimeoutError)) or (
                isinstance(exc, httpx.HTTPStatusError) and (exc.response.status_code == 429 or exc.response.status_code >= 500)
            )
            if operation:
                operation = db.get(DocumentOperation, operation.id, populate_existing=True)
                from ..ocr_backend import OCRExecutionUncertain

                uncertain = isinstance(exc, OCRExecutionUncertain)
                retryable = retryable and operations.aware(operation.execution_deadline) > datetime.now(UTC)
                if uncertain:
                    # A lost cancellation receipt cannot free the global slot.
                    operation.status = "canceling"
                else:
                    operation.status = "pending" if retryable else "failed"
                    operation.claim_token, operation.lease_until = None, None
                    operation.executor_started_at, operation.attempt_token = None, None
                operation.result = {
                    "reason": "document_dispatch_uncertain" if uncertain else "dependency_unavailable" if retryable else "ocr_failed"
                }
            state = "pending" if retryable else "failed"
            file.meta = {
                **file.meta,
                "ocr": state,
                "ocr_reason": "dependency_unavailable" if retryable else (str(exc) if isinstance(exc, ValueError) else "ocr_failed"),
            }
            from .file_dependencies import wake_file

            wake_file(db, file)
            db.commit()
        return {"ocr": state}


extraction_router = APIRouter(prefix="/v1/files", tags=["text-extraction"])


def extraction_result(db, file, pages=None):
    from .document_sources import ocr_records

    meta = file.meta or {}
    selected = sorted(set(pages or [1]))
    records = ocr_records(db, file, [p - 1 for p in selected])
    complete = all(str(p - 1) in records for p in selected)
    return {
        "ocr": "done" if complete else (meta.get("ocr", "unavailable") if meta.get("ocr") != "done" else "unavailable"),
        "file_id": file.id,
        "pages": selected,
        "completed_pages": [p for p in selected if str(p - 1) in records],
        "reason": meta.get("ocr_reason"),
        "retry_after": 1,
        "text_only": True,
        "visual_note": "OCR只提取文字，不理解图片内容、图形关系或布局；无文字图片需要视觉模型。",
    }


def request_extraction(db, file, pages=None, *, retry=False, run=None):
    pdf = file.content_type == "application/pdf" or file.filename.lower().endswith(".pdf")
    if pdf and not pages:
        raise HTTPException(422, "请选择需要提取的PDF页范围")
    if not pdf and not file.content_type.startswith("image/"):
        raise HTTPException(422, "图表或嵌图请用view_attachment；文字请读取原生文档")
    selected = sorted(set(pages or [1]))
    if not 1 <= len(selected) <= 20 or any(type(p) is not int or not 1 <= p <= 100000 for p in selected):
        raise HTTPException(422, "无效页码")
    if not pdf and selected != [1]:
        raise HTTPException(422, "独立图片只支持第1页")
    units = ((file.meta or {}).get("extract") or {}).get("units")
    if pdf and units and max(selected) > units:
        raise HTTPException(422, "所选页超出文档范围")
    meta = dict(file.meta or {})
    if meta.get("ocr") in {"pending", "running"}:
        if extraction_pages(meta) != selected:
            raise HTTPException(409, "另一组页码正在提取，请完成后重试")
        return {**extraction_result(db, file, selected), "request_id": meta.get("ocr_generation")}
    result = extraction_result(db, file, selected)
    if result["ocr"] == "done":
        if meta.get("ocr_selected_pages") != selected or not meta.get("ocr_text_key") or meta.get("ocr") != "done":
            from .document_sources import ocr_records

            publish_extraction(db, file, selected, ocr_records(db, file, [p - 1 for p in selected]), new_generation=uuid.uuid4().hex)
        return {**result, "request_id": file.meta.get("ocr_generation")}
    if meta.get("ocr") == "failed" and not retry:
        return {**result, "ocr": "failed"}
    if not settings.ocr_executor_url:
        raise HTTPException(503, {"code": "ocr_executor_unavailable", "message": "文字识别服务尚未配置"})
    if (meta.get("scan") or {}).get("verdict") != "clean":
        raise HTTPException(409, "OCR requires a clean, ready file")
    if pdf:
        missing = [p for p in selected if p not in result["completed_pages"]]
        meta["ocr_pdf"] = {"low_text_pages": [p - 1 for p in missing]}
        meta.pop("ocr_preview", None)
        meta.pop("ocr_preview_assets", None)
        meta.pop("ocr_render_job_id", None)
    meta.update(ocr="pending", ocr_reason=None, ocr_selected_pages=selected)
    file.meta = meta
    request_ocr(db, file, run=run)
    return {**result, "ocr": "pending", "request_id": file.meta["ocr_generation"]}


class ExtractionRequest(BaseModel):
    pages: list[int] | None = Field(default=None, min_length=1, max_length=20)


@extraction_router.post("/{file_id}/extract-text")
def extract_text(file_id: str, body: ExtractionRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .routes_files import _owned_file

    if body.pages and any(type(p) is not int or not 1 <= p <= 100000 for p in body.pages):
        raise HTTPException(422, "无效页码")
    with user_critical_section(db, user.id):
        file = _owned_file(db, file_id, user)
        eligible(db, file_id)
        result = request_extraction(db, file, body.pages, retry=True)
        db.commit()
    return result


def request_page_render(db, file, pages, *, run=None, preparation=None):
    from .jobs import stage_job

    meta = dict(file.meta or {})
    if (meta.get("preview") or {}).get("status") == "pending":
        # Promote the durable job as well as its transport: it may already have
        # been published to the background queue. Claims still fence duplicates.
        from ..models import OutboxMessage
        from ..queue import job_topic

        job = db.get(OutboxMessage, meta.get("preview_job_id"))
        if job:
            from .file_work import register

            register(db, file, job, run=run, preparation=preparation)
        if job and not job.claim_token and not job.payload.get("priority"):
            job.payload = {**job.payload, "priority": True}
            job.topic = job_topic("preview", priority=True)
            job.next_attempt_at = datetime.now(UTC)
        wanted = list(dict.fromkeys([*(meta.get("preview_requested_pages") or []), *(pages or [])]))[:20]
        if wanted:
            meta["preview_requested_pages"] = wanted
            file.meta = meta
        return
    job_id = uuid.uuid4().hex
    meta["preview_before_ocr"] = {k: meta[k] for k in ("preview", "preview_assets", "preview_job_id") if k in meta}
    meta.update(preview={"status": "pending", "kind": "pages"}, preview_job_id=job_id, ocr_render_job_id=job_id, ocr_render_pages=pages)
    file.meta = meta
    job = stage_job(db, "preview", file_id=file.id, job_id=job_id, priority=True)
    from .file_work import register

    register(db, file, job, run=run, preparation=preparation)


@extraction_router.get("/{file_id}/extract-text")
def extraction_status(file_id: str, request_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .routes_files import _owned_file

    file = _owned_file(db, file_id, user)
    eligible(db, file_id)
    meta = file.meta or {}
    if request_id is not None and request_id != meta.get("ocr_generation"):
        raise HTTPException(409, "提取请求已变更，请重新提取")
    return {
        "status": meta.get("ocr", "unavailable"),
        "reason": meta.get("ocr_reason"),
        "request_id": meta.get("ocr_generation"),
        "pages": extraction_pages(meta),
        "download_url": f"/v1/files/{file.id}/extracted-text?request_id={meta.get('ocr_generation', '')}"
        if meta.get("ocr_text_key") and meta.get("ocr") == "done"
        else None,
    }


@extraction_router.get("/{file_id}/extracted-text")
def extraction_download(file_id: str, request_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from fastapi.responses import StreamingResponse

    from .routes_files import _owned_file, content_disposition

    file = _owned_file(db, file_id, user)
    eligible(db, file_id)
    meta = file.meta or {}
    if request_id is not None and request_id != meta.get("ocr_generation"):
        raise HTTPException(409, "提取请求已变更，请重新提取")
    if not meta.get("ocr_text_key") or meta.get("ocr") != "done":
        raise HTTPException(409, "文字提取尚未完成")
    return StreamingResponse(
        get_storage().stream(meta["ocr_text_key"]),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": content_disposition(file.filename + ".txt")},
    )


class ChatPreparation(BaseModel):
    client_send_id: str | None = Field(default=None, min_length=1, max_length=120)
    mode: Literal["chat", "work"] = "chat"
    file_ids: list[str] = Field(max_length=50)
    model: str = Field(max_length=120)
    prompt: str = Field(default="", max_length=8000)


preparation_router = APIRouter(prefix="/v1/chat")


@preparation_router.post("/prepare")
def prepare_chat(body: ChatPreparation, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .routes_runs import validate_run_input

    with user_critical_section(db, user.id):
        validate_run_input(db, user, body.prompt, body.model, body.file_ids, body.mode, preparation_send_id=body.client_send_id)
    return {"status": "ready"}
