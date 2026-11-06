"""Explicit Chat OCR scopes, shared in-flight operations and bounded canonical text reads."""

import hashlib
import hmac
import json

from fastapi import HTTPException
from sqlalchemy import select

from ..config import settings
from ..contracts.document_input import Pages, Region
from ..domain.document_reading import CursorError, TextBlock, context_digest, decode_cursor, encode_cursor, read_batch
from ..models import (
    DocumentOcrUnit,
    FileObject,
    OcrPageResult,
    RenderedPage,
    ViewerCapture,
)
from ..storage import get_storage
from . import document_operations as operations
from .jobs import stage_job


def scopes(selector):
    if isinstance(selector, Pages):
        return [{"kind": "pages", "pages": [page]} for page in selector.pages]
    if isinstance(selector, Region):
        return [selector.model_dump(mode="json", exclude_none=True)]
    raise HTTPException(422, "explicit_ocr_scope_required")


def scope_key(selector):
    return hashlib.sha256(json.dumps(selector, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def cached(db, file, selectors):
    keys = [scope_key(selector) for selector in selectors]
    rows = db.scalars(
        select(DocumentOcrUnit)
        .where(
            DocumentOcrUnit.file_id == file.id,
            DocumentOcrUnit.source_version == file.sha256,
            DocumentOcrUnit.fingerprint == settings.ocr_engine_fingerprint,
            DocumentOcrUnit.selector_key.in_(keys),
        )
        .limit(20)
    ).all()
    found = {row.selector_key: row for row in rows}
    return [found.get(key) for key in keys]


def text_bytes(storage, row):
    if not row.text_key.startswith((f"derived/{row.file_id}/ocr-scopes/", f"derived/{row.file_id}/ocr/")) or ".." in row.text_key.split(
        "/"
    ):
        raise HTTPException(409, "ocr_cache_changed")
    data = bytearray()
    try:
        with storage.open_stream(row.text_key) as chunks:
            for chunk in chunks:
                if len(data) + len(chunk) > 800000:
                    raise HTTPException(409, "ocr_cache_changed")
                data.extend(chunk)
    except FileNotFoundError:
        raise HTTPException(409, "ocr_cache_missing") from None
    if hashlib.sha256(data).hexdigest() != row.digest:
        raise HTTPException(409, "ocr_cache_changed")
    return data


def adopt_legacy(db, file, selector):
    if not isinstance(selector, Pages) or not settings.ocr_engine_fingerprint:
        return
    selectors = scopes(selector)
    existing = cached(db, file, selectors)
    missing_pages = [scope["pages"][0] for scope, row in zip(selectors, existing, strict=True) if row is None]
    if not missing_pages:
        return
    old = db.scalars(
        select(OcrPageResult)
        .where(
            OcrPageResult.file_id == file.id,
            OcrPageResult.fingerprint == settings.ocr_engine_fingerprint,
            OcrPageResult.page.in_([page - 1 for page in missing_pages]),
        )
        .limit(20)
    ).all()
    storage = get_storage()
    counter = int((file.meta or {}).get("ocr_next_ordinal", 0))
    for row in old:
        if not row.text_key.startswith(f"derived/{file.id}/ocr/") or ".." in row.text_key.split("/"):
            raise HTTPException(409, "ocr_cache_changed")
        body = bytearray()
        with storage.open_stream(row.text_key) as chunks:
            for chunk in chunks:
                if len(body) + len(chunk) > 800000:
                    raise HTTPException(409, "ocr_cache_changed")
                body.extend(chunk)
        text = body.decode("utf-8")
        scope = {"kind": "pages", "pages": [row.page + 1]}
        db.add(
            DocumentOcrUnit(
                file_id=file.id,
                source_version=file.sha256,
                fingerprint=row.fingerprint,
                selector_key=scope_key(scope),
                selector=scope,
                ordinal=counter,
                page=row.page + 1,
                image_revision="legacy:" + row.generation,
                source_kind="legacy-page-cache",
                text_key=row.text_key,
                digest=hashlib.sha256(body).hexdigest(),
                chars=len(text),
                engine=row.engine,
                backend=row.backend,
                seconds=row.seconds,
            )
        )
        counter += 1
    if old:
        file.meta = {
            **(file.meta or {}),
            "ocr_next_ordinal": counter,
            "ocr_cache_revision": int((file.meta or {}).get("ocr_cache_revision", 0)) + 1,
        }
        db.flush()


def read_cached(db, file, request):
    selectors = scopes(request.selector)
    rows = cached(db, file, selectors)
    if any(row is None for row in rows):
        return None
    context = context_digest(
        operation="read_ocr_scope",
        file_id=file.id,
        version=file.sha256,
        representation=[(row.id, row.digest, row.image_revision) for row in rows],
        selector=request.selector.model_dump(mode="json", exclude_none=True),
    )
    key = hmac.digest(settings.caller_token("api").encode(), b"document-ocr-scope-cursor", "sha256")
    try:
        position = decode_cursor(request.cursor, context, key=key)
        storage = get_storage()

        def blocks():
            for ordinal, row in enumerate(rows):
                if ordinal < position.ordinal:
                    continue
                text = text_bytes(storage, row).decode("utf-8")
                locator = f"page:{row.page}" if row.page else "image:" + row.selector.get("image_id", row.id)
                if row.selector.get("kind") == "region":
                    locator += "/region:" + row.selector_key[:16]
                yield TextBlock(ordinal, locator, text, source="ocr", block_id=row.id)

        result = read_batch(blocks(), position)
    except CursorError as exc:
        raise HTTPException(409, str(exc)) from None
    except UnicodeError:
        raise HTTPException(409, "ocr_cache_changed") from None
    next_position = result.pop("next_position")
    return {
        "file_id": file.id,
        "version": file.sha256,
        "text_source": "ocr",
        **result,
        "next_cursor": encode_cursor(context, next_position, key=key) if next_position else None,
        "truncated": next_position is not None,
        "representations": [
            {
                "selector": row.selector,
                "revision": row.image_revision,
                "source": row.source_kind,
                "engine": row.engine,
                "text_revision": row.id,
                **(row.details or {}),
            }
            for row in rows
        ],
        "coverage": {
            "native": {"status": "not_read"},
            "visual": {"status": "not_viewed"},
            "ocr": {"status": "read_selected", "scopes": selectors, "blocks": result.get("coverage", [])},
        },
    }


def request_scope(db, run, file, request):
    if request.cursor:
        raise HTTPException(409, "cursor_stale")
    if not settings.ocr_executor_url or not settings.ocr_engine_fingerprint:
        raise HTTPException(503, "ocr_executor_unavailable")
    selectors = scopes(request.selector)
    present = cached(db, file, selectors)
    missing = [selector for selector, row in zip(selectors, present, strict=True) if row is None]
    if not missing:
        return None
    known_pages = [selector["pages"][0] if selector["kind"] == "pages" else selector.get("page") for selector in missing]
    physical = sorted({page for page in known_pages if page is not None})
    source_pages = ["page:" + str(page) for page in physical]
    source_pages.extend("image:" + selector["image_id"] for selector in missing if selector.get("image_id"))
    identity = {"selectors": selectors, "engine": settings.ocr_engine_fingerprint, "renderer": "poppler-1280-v1"}
    binding = {
        "selectors": selectors,
        "pages": physical or [1],
        "source_pages": source_pages,
        "identity": identity,
        "engine": settings.ocr_engine_fingerprint,
        "renderer": "poppler-1280-v1",
    }
    operation = operations.request(
        db, run, lane="ocr", kind="chat_ocr", version=file.sha256, binding=binding, file_id=file.id, priority=1, share=True
    )
    if operation.status in {"failed", "canceled"}:
        return {
            "file_id": file.id,
            "version": file.sha256,
            "error": (operation.result or {}).get("reason", "ocr_failed"),
            "partial": any(row is not None for row in present),
            "completed_scopes": [row.selector for row in present if row],
        }
    if not operation.job_id:
        operation.job_id = operation.id
        job = stage_job(db, "document_ocr", file_id=file.id, job_id=operation.id, priority=True)
        job.payload = {**job.payload, "deadline": operations.aware(operation.deadline).isoformat()}
    return {
        "file_id": file.id,
        "version": file.sha256,
        "pending": True,
        "action": "document-ocr",
        "operation_id": operation.id,
        "deadline": min(operations.budget(run), operations.aware(operation.deadline)).timestamp(),
        "pages": physical or None,
    }


def consumer_file(db, operation):
    file = db.get(FileObject, operation.file_id, populate_existing=True)
    if not file or file.deleted_at or file.purged_at or file.sha256 != operation.source_version:
        raise HTTPException(409, "document_source_unavailable")
    if not operations.source_available(db, operation, active=True):
        raise HTTPException(409, "document_consumers_unavailable")
    return file


def visual_input(db, operation, file, selector):
    """Resolve exactly the requested representation; never infer a Word page."""
    storage = get_storage()
    standalone_image = file.content_type.startswith("image/") and selector.get("image_id") == file.id
    if selector["kind"] == "region" and selector.get("image_id") and not standalone_image:
        capture = db.get(ViewerCapture, selector["image_id"])
        if (
            not capture
            or capture.file_id != file.id
            or capture.target.get("version") != file.sha256
            or capture.digest != selector["revision"]
        ):
            raise HTTPException(409, "ocr_image_revision_changed")
        run = operations.live_consumer(db, operation)
        from .viewer_captures import image

        image(db, run.id, capture.id)  # ownership, branch, source/version and bounded digest verification
        return {
            "key": capture.storage_key,
            "digest": capture.digest,
            "revision": capture.digest,
            "source": "browser",
            "region": selector,
            "page": 0,
        }
    page = selector["pages"][0] if selector["kind"] == "pages" else 1 if standalone_image else selector["page"]
    if file.content_type.startswith("image/"):
        if page != 1:
            raise HTTPException(422, "document_page_out_of_range")
        key = (file.meta or {}).get("display_key")
        if not key:
            from .preview_requests import request_preview

            request_preview(db, file)
            return None
        # Image representations may have ETags other than SHA256; expose the
        # exact representation revision and independently verify transfer size.
        info = storage.info(key)
        revision = info.etag
        if selector["kind"] == "region" and selector["revision"] != revision:
            raise HTTPException(409, "ocr_image_revision_changed")
        return {
            "key": key,
            "digest": revision if len(revision) == 64 else None,
            "revision": revision,
            "source": "normalized-image",
            "region": selector if selector["kind"] == "region" else None,
            "page": 1,
        }
    row = db.get(RenderedPage, (file.id, page, "poppler-1280-v1"))
    if not row:
        from .routes_ocr import request_page_render

        request_page_render(db, file, [page])
        return None
    if selector["kind"] == "region" and selector["revision"] != row.digest:
        raise HTTPException(409, "ocr_image_revision_changed")
    return {
        "key": row.key,
        "digest": row.digest,
        "revision": row.digest,
        "source": "native-pdf" if file.content_type == "application/pdf" else "office-pdf",
        "region": selector if selector["kind"] == "region" else None,
        "page": page,
    }
