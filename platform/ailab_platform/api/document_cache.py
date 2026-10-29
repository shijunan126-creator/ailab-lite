"""Private cache eligibility only. Worker copies objects outside business transactions."""

from sqlalchemy import select

from ..models import FileObject

PARSER_VERSION = "zcode-3c5b227-ailab2"


def reuse(db, file):
    candidate = db.scalar(
        select(FileObject)
        .where(
            FileObject.user_id == file.user_id,
            FileObject.sha256 == file.sha256,
            FileObject.id != file.id,
            FileObject.status == "ready",
            FileObject.deleted_at.is_(None),
            FileObject.purged_at.is_(None),
            FileObject.content_type == file.content_type,
        )
        .order_by(FileObject.created_at.desc())
        .limit(1)
    )
    if not candidate:
        return None
    source = candidate.meta or {}
    native = (
        (source.get("extract") or {}).get("parser") == PARSER_VERSION
        and source.get("document_manifest_key")
        and source["extract"].get("coverage") == "ready"
    )
    from ..config import settings
    from ..models import OcrPageResult

    ocr = bool(
        settings.ocr_engine_fingerprint
        and db.scalar(
            select(OcrPageResult.file_id)
            .where(OcrPageResult.file_id == candidate.id, OcrPageResult.fingerprint == settings.ocr_engine_fingerprint)
            .limit(1)
        )
    )
    image = (file.content_type or "").startswith("image/")
    pdf = file.content_type == "application/pdf" or file.filename.lower().endswith(".pdf")
    if not native and not ((image or pdf) and ocr):
        return None
    return {
        "source_file_id": candidate.id,
        "needs_native": not native and pdf,
        "manifest_key": source.get("document_manifest_key") if native else None,
        "text_key": source.get("text_key"),
        "extract": source.get("extract") or {},
        "image_keys": {key: source[key] for key in ("display_key", "thumb_key") if source.get(key)},
        "ocr_text_key": source.get("ocr_text_key") if ocr and source.get("ocr") == "done" else None,
        "ocr_cache": ocr,
    }


def pair(db, target_id, source_id):
    from fastapi import HTTPException

    target, source = db.get(FileObject, target_id, populate_existing=True), db.get(FileObject, source_id, populate_existing=True)
    if (
        not target
        or not source
        or target.status != "pending"
        or source.status != "ready"
        or target.user_id != source.user_id
        or target.sha256 != source.sha256
        or target.content_type != source.content_type
        or target.deleted_at
        or target.purged_at
        or source.deleted_at
        or source.purged_at
    ):
        raise HTTPException(409, "cache_source_unavailable")
    return target, source
