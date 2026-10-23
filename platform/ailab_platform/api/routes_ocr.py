"""File-bound OCR: only the API resolves credentials and writes usage/files.

The Worker queues one job per file in the READY transaction (request_ocr) and drives it through
prepare -> preview -> run. file.meta.ocr moves pending -> running -> done|failed; unavailable comes
only from configuration. Each page is one usage call (ocr:<file>:<page>) billed to the file owner.
"""

import asyncio
import threading
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..agent.multimodal import image_part
from ..catalog import get_catalog
from ..config import settings
from ..contracts.usage import ReserveUsageIn, SettleUsageIn
from ..deps import get_db
from ..llm import ModelCall, llm_for, model_call
from ..locks import user_critical_section
from ..model_policy import ModelPolicy, estimator
from ..models import FileObject, FileStatus, UsageReservation
from ..quota import reserve_usage, settle_usage
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
_PROMPT = "请逐字识别图片中的文字，保留段落与表格。只返回识别文本；无法辨认请明确说明。"

router = APIRouter(prefix="/internal/files", dependencies=[Depends(require_scope("files:ocr"))])


def eligible(db, file_id):
    file = db.get(FileObject, file_id, populate_existing=True)
    if not file or file.deleted_at or file.purged_at:
        raise HTTPException(404, "file not found")
    if file.status != FileStatus.READY or ((file.meta or {}).get("scan") or {}).get("verdict") != "clean":
        raise HTTPException(409, "OCR requires a clean, ready file")
    return file


def available():
    spec = get_catalog().get(settings.ocr_alias)
    return spec if spec and spec.available and spec.supports({"vision"}) else None


def request_ocr(db: Session, file: FileObject) -> None:
    """Runs in the READY patch's transaction, so the job exists exactly when OCR is pending."""
    from .jobs import stage_job

    file.meta = {**(file.meta or {}), "ocr_requested_at": datetime.now(UTC).timestamp()}
    # A durable job like any other: claimed, ACKed by the Worker, and only then removed from the outbox.
    stage_job(db, "ocr", file_id=file.id)


def _total_timeout() -> float:
    return settings.llm_total_timeout_seconds or settings.llm_timeout_seconds


def update(db, file, **fields):
    with user_critical_section(db, file.user_id):
        file = eligible(db, file.id)
        file.meta = {**(file.meta or {}), **fields}
        db.commit()
    return fields


@router.post("/{file_id}/ocr/prepare")
def prepare(file_id: str, db: Session = Depends(get_db)):
    file = eligible(db, file_id)
    with user_critical_section(db, file.user_id):
        file = eligible(db, file_id)
        meta = dict(file.meta or {})
        now = datetime.now(UTC).timestamp()

        def finish(**fields):
            file.meta = {**meta, **fields}
            db.commit()
            return fields

        if meta.get("ocr") == "running":
            if now - meta.get("ocr_started_at", 0) > _total_timeout() + 30:
                return finish(ocr="failed", ocr_reason="interrupted")
            return {"ocr": "running"}
        if meta.get("ocr") != "pending":
            return {"ocr": meta.get("ocr", "unavailable")}
        if now - meta.get("ocr_requested_at", now) > settings.ocr_deadline_seconds:
            return finish(ocr="failed", ocr_reason="deadline")
        if not available():
            return finish(ocr="unavailable", ocr_reason="vision_not_configured")
        if file.size > settings.ocr_max_bytes:
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
        if len(pdf["low_text_pages"]) > settings.ocr_max_pages:
            return finish(ocr="failed", ocr_reason="page_limit")
        preview = meta.get("preview") or {}
        if preview.get("status") not in {"ready", "pending"}:
            job_id = uuid.uuid4().hex
            meta.update(preview={"status": "pending", "kind": "pages"}, preview_job_id=job_id, preview_requested_at=now)
            from .jobs import stage_job

            stage_job(db, "preview", file_id=file.id, job_id=job_id)
        file.meta = dict(meta)
        db.commit()
        pending = (meta.get("preview") or {}).get("status") == "pending"
        return {"ocr": "pending", "preview_job_id": meta["preview_job_id"] if pending else None}


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


async def recognize(db, file, spec):
    meta = file.meta or {}
    pdf = meta.get("ocr_pdf")
    if pdf is None:
        targets = [(0, meta["display_key"], "image/webp")]
    else:
        preview = meta.get("preview") or {}
        names = preview.get("pages") or []
        if preview.get("status") != "ready":
            raise ValueError("preview_unavailable")
        if max(pdf["low_text_pages"]) >= len(names):  # the preview renders a bounded prefix of the document
            raise ValueError("page_limit")
        assets = meta.get("preview_assets") or {}
        targets = []
        for page in pdf["low_text_pages"]:
            asset = assets.get(names[page]) or {}
            if not asset.get("key"):
                raise ValueError("preview_unavailable")
            targets.append((page, asset["key"], "image/jpeg"))
    results = {}
    for page, key, mime in targets:
        messages = [{"role": "user", "content": [{"type": "text", "text": _PROMPT}, image_part(bounded_image(key, file.id), mime)]}]
        estimate = estimator.estimate(spec.id, messages)
        output = min(spec.max_output_tokens, settings.ocr_max_output_tokens)
        if estimate > ModelPolicy.input_budget(spec) or estimate + output > spec.context_tokens:
            raise ValueError("context_limit")
        call_id = f"ocr:{file.id}:{page}"
        # A redelivered job must not issue a second inference for a page that already has a call.
        if db.get(UsageReservation, call_id):
            raise ValueError("call_already_dispatched")
        reserve_usage(
            db,
            ReserveUsageIn(
                call_id=call_id,
                subject="file_job",
                file_id=file.id,
                attempt_id=call_id,
                alias=spec.id,
                mode="ocr",
                max_output_tokens=output,
                estimated_input_tokens=estimate,
            ),
        )
        call = ModelCall(call_id, "", output)
        token = model_call.set(call)
        response = None
        try:
            response = await llm_for(spec.id).chat(messages, [], spec.id, user_id=file.user_id)
            if not response.content.strip() or response.tool_calls or response.finish_reason != "stop":
                raise ValueError("invalid_ocr_response")
            estimator.calibrate(spec.id, messages, response.usage.get("prompt_tokens", 0))
            results[page] = response.content.strip()
        finally:
            model_call.reset(token)
            usage = response.usage if response is not None else call.usage
            status = "settled" if usage else "estimated" if call.reached_model else "released"
            settle_usage(
                db,
                SettleUsageIn(
                    call_id=call_id,
                    attempt_id=call_id,
                    status=status,
                    actual_backend=call.actual_backend,
                    input_tokens=usage.get("prompt_tokens", estimate if status == "estimated" else 0),
                    cached_tokens=usage.get("cached_tokens", 0),
                    output_tokens=usage.get("completion_tokens", output if status == "estimated" else 0),
                    reasoning_tokens=usage.get("reasoning_tokens", 0),
                ),
            )
        if sum(len(text) for text in results.values()) > 200_000:
            raise ValueError("text_limit")
    text = results[0] if pdf is None else "\n\n".join(f"第 {page + 1} 页\n{results[page]}" for page in sorted(results))
    if not text.strip():
        raise ValueError("empty_ocr_text")
    return text


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


def _run_ocr(file_id: str, db: Session):
    file = eligible(db, file_id)
    spec = available()
    if spec is None:
        return update(db, file, ocr="unavailable", ocr_reason="vision_not_configured")
    with user_critical_section(db, file.user_id):
        file = eligible(db, file_id)
        if (file.meta or {}).get("ocr") != "pending":
            return {"ocr": (file.meta or {}).get("ocr", "unavailable")}
        file.meta = {**file.meta, "ocr": "running", "ocr_started_at": datetime.now(UTC).timestamp()}
        db.commit()
    try:

        async def invoke():
            async with asyncio.timeout(_total_timeout()):
                return await recognize(db, file, spec)

        text = asyncio.run(invoke())
        key = f"derived/{file_id}/ocr.txt"
        # Recheck deletion before writing derived objects, under the owner lock. The native text key is untouched.
        with user_critical_section(db, file.user_id):
            file = eligible(db, file_id)
            get_storage().put_bytes(key, text.encode(), "text/plain; charset=utf-8")
            file.meta = {**file.meta, "ocr": "done", "ocr_text_key": key}
            db.commit()
        return {"ocr": "done", "chars": len(text)}
    except Exception as exc:
        db.rollback()
        return update(db, file, ocr="failed", ocr_reason=_reason(exc))


# Conversation image reads use the existing active-run attachment authorization,
# not Worker OCR permission. The only exposed image is a normalized derivative.
vision_router = APIRouter(prefix="/internal/files")


@vision_router.get("/{file_id}/vision")
def vision_image(
    file_id: str,
    run_id: str,
    principal=Depends(require_scope("files:read_for_run")),
    db: Session = Depends(get_db),
):
    from .routes_internal import _bound_file

    file = _bound_file(db, file_id, principal, run_id, "processing")
    key = (file.meta or {}).get("display_key")
    if not key:
        raise HTTPException(409, "normalized image unavailable")
    try:
        return Response(bounded_image(key, file_id), media_type="image/webp")
    except ValueError:
        raise HTTPException(413, "image size limit") from None
