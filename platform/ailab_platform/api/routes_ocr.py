"""Explicit file-bound RapidOCR jobs. API owns unbilled page/engine/time records; Worker drives durable progress."""

import asyncio
import threading
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
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
    if file.status != FileStatus.READY or ((file.meta or {}).get("scan") or {}).get("verdict") != "clean":
        raise HTTPException(409, "OCR requires a clean, ready file")
    return file


def request_ocr(db: Session, file: FileObject) -> None:
    """Runs in the READY patch's transaction, so the job exists exactly when OCR is pending."""
    from .jobs import stage_job

    file.meta = {**(file.meta or {}), "ocr_requested_at": datetime.now(UTC).timestamp(), "ocr_generation": uuid.uuid4().hex}
    # A durable job like any other: claimed, ACKed by the Worker, and only then removed from the outbox.
    stage_job(db, "ocr", file_id=file.id)


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
def prepare(file_id: str, db: Session = Depends(get_db)):
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
            if ((file.meta or {}).get("scan") or {}).get("verdict") != "clean":
                file.meta = {**(file.meta or {}), "ocr": "failed", "ocr_reason": "scan_required"}
                db.commit()
                return {"ocr": "failed", "reason": "scan_required"}
    file = eligible(db, file_id)
    with user_critical_section(db, file.user_id):
        file = eligible(db, file_id)
        meta = dict(file.meta or {})
        now = datetime.now(UTC).timestamp()

        def finish(**fields):
            file.meta = {**meta, **fields}
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
                return finish(ocr="failed", ocr_reason="normalized_image_unavailable")
            return {"ocr": "pending"}
        if pdf.get("error"):
            return finish(ocr="failed", ocr_reason="pdf_analysis_unavailable")
        if not pdf["low_text_pages"]:
            return finish(ocr="done", ocr_reason="text_pdf")

        preview = meta.get("ocr_preview") or {}
        if preview.get("status") == "ready":
            return {"ocr": "pending"}
        if meta.get("ocr_render_job_id") and (meta.get("preview") or {}).get("status") == "pending":
            return {"ocr": "pending", "preview_job_id": meta["ocr_render_job_id"]}
        job_id = uuid.uuid4().hex
        meta["preview_before_ocr"] = {k: meta[k] for k in ("preview", "preview_assets", "preview_job_id") if k in meta}
        meta.update(
            preview={"status": "pending", "kind": "pages"},
            preview_job_id=job_id,
            ocr_render_job_id=job_id,
            ocr_render_pages=[p + 1 for p in pdf["low_text_pages"]],
        )
        file.meta = meta
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
def run_ocr(file_id: str, db: Session = Depends(get_db)):
    if not _ocr_slot.acquire(blocking=False):
        raise HTTPException(429, "OCR busy; retry later")
    try:
        return _run_ocr(file_id, db)
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


def _run_ocr(file_id: str, db: Session):
    from ..ocr_backend import recognize as remote_recognize

    file = eligible(db, file_id)
    if not settings.ocr_executor_url:
        return update(db, file, ocr="unavailable", ocr_reason="ocr_executor_unavailable")
    with user_critical_section(db, file.user_id):
        file = eligible(db, file_id)
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
            for page in meta["ocr_pdf"]["low_text_pages"]:
                name = next((n for n in names if int(n[2:-4]) == page + 1), None)
                if name is None:
                    raise HTTPException(422, "OCR page render unavailable")
                targets.append((page, (assets.get(name) or {}).get("key")))
        file.meta = {**meta, "ocr": "running", "ocr_started_at": datetime.now(UTC).timestamp()}
        db.commit()
    try:
        texts = []
        for page, key in targets:
            if datetime.now(UTC).timestamp() - requested_at > settings.ocr_deadline_seconds:
                raise ValueError("deadline")
            if not key:
                raise ValueError("preview_unavailable")
            file = eligible(db, file_id)
            from .document_sources import ocr_records

            done = ocr_records(db, file, [p for p, _ in targets])
            record = done.get(str(page))
            db.rollback()  # no DB transaction or owner lock across remote inference
            if record and (
                record.get("generation") == generation
                or (settings.ocr_engine_fingerprint and record.get("fingerprint") == settings.ocr_engine_fingerprint)
            ):
                text = get_storage().get_bytes(record["text_key"]).decode()
            else:
                result = asyncio.run(remote_recognize(bounded_image(key, file_id)))
                text = result["text"]
                with user_critical_section(db, file.user_id):
                    file = eligible(db, file_id)
                    if (
                        str(file.meta.get("ocr_generation") or file.meta.get("ocr_requested_at")) != generation
                        or file.meta.get("ocr") != "running"
                    ):
                        raise HTTPException(409, "OCR source changed")
                    text_key = f"derived/{file_id}/ocr/{generation}/{page}.txt"
                    get_storage().put_bytes(text_key, text.encode(), "text/plain; charset=utf-8")
                    pages = dict(done)
                    from ..models import OcrPageResult

                    fingerprint = settings.ocr_engine_fingerprint or generation
                    page_row = db.get(OcrPageResult, (file.id, page, fingerprint))
                    if page_row is None:
                        page_row = OcrPageResult(file_id=file.id, page=page, fingerprint=fingerprint)
                        db.add(page_row)
                    page_row.generation, page_row.text_key = generation, text_key
                    page_row.engine, page_row.backend = result["engine"], result["backend"]
                    page_row.seconds = result.get("seconds", 0)
                    pages[str(page)] = {
                        "generation": generation,
                        "text_key": text_key,
                        "engine": result["engine"],
                        "fingerprint": result.get("fingerprint"),
                        "backend": result["backend"],
                        "seconds": result.get("seconds", 0),
                        "pages": 1,
                    }
                    more = any(str(p) not in pages or pages[str(p)].get("generation") != generation for p, _ in targets)
                    file.meta = {
                        **file.meta,
                        "ocr_pages": dict(list(pages.items())[-20:]),
                        "ocr_cache_revision": int(file.meta.get("ocr_cache_revision", 0)) + 1,
                        "ocr": "pending" if more else "running",
                    }
                    db.commit()
                    if more:
                        return {"ocr": "pending", "completed_page": page + 1}
            texts.append(f"\f[page:{page + 1}]\n{text}")
        with user_critical_section(db, file.user_id):
            file = eligible(db, file_id)
            if str(file.meta.get("ocr_generation") or file.meta.get("ocr_requested_at")) != generation:
                raise HTTPException(409, "OCR source changed")
            text_key = f"derived/{file_id}/ocr.txt"
            get_storage().put_bytes(text_key, "\n".join(texts).encode(), "text/plain; charset=utf-8")
            # OCR extraction never schedules a file-summary LLM call.
            file.meta = {**file.meta, "ocr": "done", "ocr_text_key": text_key}
            from .file_dependencies import wake_file

            wake_file(db, file)
            db.commit()
        return {"ocr": "done", "pages": len(targets), "chars": sum(map(len, texts))}
    except Exception as exc:
        db.rollback()
        with user_critical_section(db, file.user_id):
            file = eligible(db, file_id)
            if str(file.meta.get("ocr_generation") or file.meta.get("ocr_requested_at")) != generation:
                return {"ocr": "canceled", "reason": "superseded"}
            import httpx

            retryable = isinstance(exc, (httpx.TransportError, TimeoutError)) or (
                isinstance(exc, httpx.HTTPStatusError) and (exc.response.status_code == 429 or exc.response.status_code >= 500)
            )
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


def request_extraction(db, file, pages=None, *, retry=False):
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
    result = extraction_result(db, file, selected)
    if result["ocr"] == "done":
        return result
    meta = dict(file.meta or {})
    if meta.get("ocr") == "failed" and not retry:
        return {**result, "ocr": "failed"}
    if not settings.ocr_executor_url:
        raise HTTPException(503, {"code": "ocr_executor_unavailable", "message": "文字识别服务尚未配置"})
    if meta.get("ocr") in {"pending", "running"}:
        # A different page selection waits for the current job rather than replacing
        # its generation. The next call can start missing pages after it finishes.
        return {**result, "ocr": meta["ocr"]}
    if (meta.get("scan") or {}).get("verdict") != "clean":
        raise HTTPException(409, "OCR requires a clean, ready file")
    if pdf:
        missing = [p for p in selected if p not in result["completed_pages"]]
        meta["ocr_pdf"] = {"low_text_pages": [p - 1 for p in missing]}
        meta.pop("ocr_preview", None)
    meta.update(ocr="pending", ocr_reason=None)
    file.meta = meta
    request_ocr(db, file)
    return {**result, "ocr": "pending"}


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


def request_page_render(db, file, pages):
    from .jobs import stage_job

    meta = dict(file.meta or {})
    if (meta.get("preview") or {}).get("status") == "pending":
        # Promote the durable job as well as its transport: it may already have
        # been published to the background queue. Claims still fence duplicates.
        from ..models import OutboxMessage
        from ..queue import job_topic

        job = db.get(OutboxMessage, meta.get("preview_job_id"))
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
    stage_job(db, "preview", file_id=file.id, job_id=job_id, priority=True)


@extraction_router.get("/{file_id}/extract-text")
def extraction_status(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .routes_files import _owned_file

    file = _owned_file(db, file_id, user)
    eligible(db, file_id)
    meta = file.meta or {}
    return {
        "status": meta.get("ocr", "unavailable"),
        "reason": meta.get("ocr_reason"),
        "pages": sorted(int(p) + 1 for p in (meta.get("ocr_pages") or {})),
        "download_url": f"/v1/files/{file.id}/extracted-text" if meta.get("ocr_text_key") else None,
    }


@extraction_router.get("/{file_id}/extracted-text")
def extraction_download(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from fastapi.responses import StreamingResponse

    from .routes_files import _owned_file, content_disposition

    file = _owned_file(db, file_id, user)
    eligible(db, file_id)
    meta = file.meta or {}
    if not meta.get("ocr_text_key") or meta.get("ocr") != "done":
        raise HTTPException(409, "文字提取尚未完成")
    return StreamingResponse(
        get_storage().stream(meta["ocr_text_key"]),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": content_disposition(file.filename + ".txt")},
    )


class ChatPreparation(BaseModel):
    mode: Literal["chat", "work"] = "chat"
    file_ids: list[str] = Field(max_length=50)
    model: str = Field(max_length=120)
    prompt: str = Field(default="", max_length=8000)


preparation_router = APIRouter(prefix="/v1/chat")


@preparation_router.post("/prepare")
def prepare_chat(body: ChatPreparation, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .routes_runs import validate_run_input

    with user_critical_section(db, user.id):
        validate_run_input(db, user, body.prompt, body.model, body.file_ids, body.mode)
    return {"status": "ready"}
