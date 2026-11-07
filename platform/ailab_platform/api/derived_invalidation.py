"""Remove business availability in the same transaction as cache retirement."""

from sqlalchemy import delete, or_, select

from ..models import DocumentBlock, DocumentOcrUnit, FileDerivation, FileObject, OcrPageResult, RenderedPage, ViewerCapture


def invalidate(db, row):
    key = row.key
    db.execute(delete(RenderedPage).where(RenderedPage.key == key))
    db.execute(delete(OcrPageResult).where(OcrPageResult.text_key == key))
    db.execute(delete(DocumentOcrUnit).where(DocumentOcrUnit.text_key == key))
    db.execute(delete(ViewerCapture).where(ViewerCapture.storage_key == key))
    derivations = select(DocumentBlock.derivation_id).where(DocumentBlock.text_key == key)
    db.execute(delete(FileDerivation).where(or_(FileDerivation.manifest_key == key, FileDerivation.id.in_(derivations))))
    if not row.file_id:
        return
    file = db.get(FileObject, row.file_id, populate_existing=True)
    if not file:
        return
    meta = dict(file.meta or {})
    changed, preview, ocr_preview, native, ocr = False, False, False, False, False
    for field in ("viewer_key", "derived_key", "text_key", "document_manifest_key", "display_key", "thumb_key", "ocr_text_key"):
        if meta.get(field) == key:
            meta.pop(field)
            changed = True
            native |= field in {"text_key", "document_manifest_key"}
            ocr |= field == "ocr_text_key"
            preview |= field == "display_key"
    for field in ("preview_assets", "ocr_preview_assets"):
        values = meta.get(field) or {}
        filtered = {name: asset for name, asset in values.items() if not isinstance(asset, dict) or asset.get("key") != key}
        if len(filtered) != len(values):
            meta[field] = filtered
            changed = True
            preview |= field == "preview_assets"
            ocr_preview |= field == "ocr_preview_assets"
    view_files = (meta.get("skill_view") or {}).get("files") or ()
    if key.startswith(f"derived/{file.id}/skill/") and any(item.get("blob") == key for item in view_files):
        # The next view unpacks again rather than listing a file it can no longer serve.
        meta.pop("skill_view")
        changed = True
    if preview:
        meta.pop("preview", None)
    if ocr_preview:
        meta.pop("ocr_preview", None)
    if native:
        representations = dict(meta.get("representations") or {})
        representations["native_text"] = {"status": "absent", "version": file.sha256}
        meta["representations"] = representations
        meta.pop("extract", None)
    if ocr:
        meta["ocr"] = "absent"
    if changed:
        file.meta = meta
