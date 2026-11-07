"""Canonical OCR page publication under the existing file extraction contract."""

from fastapi import HTTPException

from ..config import settings
from ..models import OcrPageResult
from . import document_operations as operations
from .owned_assets import publish


def store_page(db, file, generation, page, result, *, attempt, operation=None):
    from .document_sources import ocr_records
    from .routes_ocr import check_ocr_attempt, eligible

    file_id, owner, version = file.id, file.user_id, file.sha256
    operation_id = operation.id if operation else None
    token = operation.claim_token if operation else None
    key = f"derived/{file_id}/ocr/{generation}/{page}.txt"

    def validate():
        current = eligible(db, file_id)
        check_ocr_attempt(db, current, attempt)
        if (
            current.sha256 != version
            or str(current.meta.get("ocr_generation") or current.meta.get("ocr_requested_at")) != generation
            or current.meta.get("ocr") != "running"
        ):
            raise HTTPException(409, "OCR source changed")
        if operation_id:
            operations.active(db, operation_id, token)

    def bind(text_key):
        current = eligible(db, file_id)
        fingerprint = settings.ocr_engine_fingerprint or generation
        row = db.get(OcrPageResult, (file_id, page, fingerprint))
        if row is None:
            row = OcrPageResult(file_id=file_id, page=page, fingerprint=fingerprint)
            db.add(row)
        row.generation, row.text_key = generation, text_key
        row.engine, row.backend, row.seconds = result["engine"], result["backend"], result.get("seconds", 0)
        pages = ocr_records(db, current, [page])
        pages = {**(current.meta.get("ocr_pages") or {}), **pages}
        pages[str(page)] = {
            "generation": generation,
            "text_key": text_key,
            "engine": result["engine"],
            "fingerprint": result.get("fingerprint"),
            "backend": result["backend"],
            "seconds": result.get("seconds", 0),
            "pages": 1,
        }
        current.meta = {
            **current.meta,
            "ocr_pages": dict(list(pages.items())[-20:]),
            "ocr_cache_revision": int(current.meta.get("ocr_cache_revision", 0)) + 1,
            "ocr": "running",
        }
        return text_key

    return publish(
        db, owner, key, result["text"].encode(), version, kind="ocr", validate=validate, bind=bind, content_type="text/plain; charset=utf-8"
    )
