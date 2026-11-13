"""Deterministic mode-specific input assembly; no untrusted parsing here."""

from fastapi import HTTPException
from sqlalchemy import select

from ..catalog import get_catalog
from ..models import FileObject
from ..storage import get_storage
from .document_content import unit_bytes, visual_info
from .document_sources import attachment_manifest

MAX_CHAT_CHARS = 200000


def prepare_native_input(db, file):
    """Consumption, never upload, asks for a bounded native representation."""
    if (file.meta or {}).get("input_lifecycle") != 2 or (file.meta or {}).get("document_manifest_key"):
        return
    from ..locks import user_critical_section
    from .native_preparation import request_native

    with user_critical_section(db, file.user_id):
        db.refresh(file)
        state = request_native(db, file, full_pdf=True)
        db.commit()
    if state["status"] == "failed":
        raise HTTPException(
            422,
            {"code": "chat_attachment_unavailable", "reason": state.get("reason"), "message": "附件文字准备失败，原文件仍可用于工作模式"},
        )
    if state["status"] != "ready":
        raise HTTPException(409, {"code": "chat_attachment_processing", "message": "附件正在准备文字"})
    if not (file.meta or {}).get("document_manifest_key"):
        raise HTTPException(409, {"code": "native_cache_missing", "message": "附件文字缓存不可用，原文件仍保留"})


def prepare_input_images(db, files):
    for fid in files:
        file = db.get(FileObject, fid)
        meta = file.meta or {}
        if meta.get("input_lifecycle") != 2 or not file.content_type.startswith("image/") or meta.get("display_key"):
            continue
        from ..locks import user_critical_section
        from .preview_requests import request_preview

        with user_critical_section(db, file.user_id):
            db.refresh(file)
            if (file.meta or {}).get("display_key"):
                continue
            preview, _ = request_preview(db, file)
            db.commit()
        if preview["status"] == "pending":
            raise HTTPException(409, {"code": "chat_attachment_processing", "message": "附件正在准备图片"})
        raise HTTPException(422, {"code": "attachment_image_unavailable", "message": "图片无法准备为模型输入，原文件仍保留"})


def _too_large():
    raise HTTPException(422, {"code": "chat_attachment_too_large", "message": "文件超出对话预算，请选择范围或切换工作模式"})


def readable_text(file, limit=MAX_CHAT_CHARS, db=None):
    meta = file.meta or {}
    if meta.get("document_manifest_key"):
        import json

        storage = get_storage()
        manifest = json.loads(storage.get_bytes(meta["document_manifest_key"]))
        from .document_content import native_text, unit_text
        from .document_sources import ocr_records

        pages = [unit["unit_number"] - 1 for unit in manifest["units"] if unit["anchor"].startswith("page:")]
        records = ocr_records(db, file, pages[:3000])
        parts, chars, native_cache = [], 0, {}
        for unit in manifest["units"]:
            raw = unit_bytes(file, unit, native_cache)
            text, method = unit_text(db, file, unit, raw, records)
            if (
                unit["anchor"].startswith("page:")
                and unit.get("visual", True)
                and not native_text(file, unit, raw).strip()
                and method != "ocr"
            ):
                return "", False
            chars += len(text)
            if chars > limit:
                return "", False
            parts.append(text)
        return "\n\n".join(parts), manifest["status"] == "ready"
    key = meta.get("ocr_text_key") if meta.get("ocr") == "done" else meta.get("text_key")
    if not key:
        return "", False
    import codecs

    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    parts, chars = [], 0
    with get_storage().open_stream(key) as chunks:
        for chunk in chunks:
            part = decoder.decode(chunk)
            chars += len(part)
            if chars > limit:
                return "", False
            parts.append(part)
        parts.append(decoder.decode(b"", final=True))
    return "".join(parts), True


def chat_file(file, spec, db=None):
    meta = file.meta or {}
    extract = meta.get("extract") or {}
    image = (file.content_type or "").startswith("image/")
    if image and spec.supports({"vision"}):
        return ""
    if not image and (extract.get("truncated") or extract.get("kind") in {"error", "too_large", "binary"}):
        raise HTTPException(422, {"code": "chat_attachment_too_large", "message": "文件未完整预处理，请选择范围或切换工作模式"})
    from .document_content import manifest, visual_info

    doc = manifest(file)
    visual = visual_info(file, doc)
    if spec.supports({"vision"}) and doc and visual["status"] != "none":
        parts, chars, native_cache = [], 0, {}
        from .document_content import native_text

        for unit in doc["units"]:
            text = native_text(file, unit, unit_bytes(file, unit, native_cache))
            chars += len(text)
            if chars > MAX_CHAT_CHARS:
                _too_large()
            parts.append(text)
        return "\n\n".join(parts)
    text, complete = readable_text(file, db=db)
    if not complete:
        raise HTTPException(422, {"code": "chat_attachment_unavailable", "message": "文件文字尚不可读，请稍候或切换工作模式"})
    return text


def input_files(db, run, *, document_plan=None, **message):
    refs = attachment_manifest(db, run, **message)
    if not refs:
        return []
    if (run.options or {}).get("mode", "work") == "chat":
        from .document_input_plan import input_files as planned_input_files

        return planned_input_files(db, run, refs, plan=document_plan)
    spec = get_catalog().get(run.model)
    files = {f.id: f for f in db.scalars(select(FileObject).where(FileObject.id.in_([r["file_id"] for r in refs])))}
    out = []
    for ref in refs:
        f = files[ref["file_id"]]
        meta = f.meta or {}
        image = (f.content_type or "").startswith("image/")
        out.append(
            {
                **ref,
                "id": f.id,
                "input_strategy": "prepared" if image and spec and spec.supports({"vision"}) else "reference",
                "size": f.size,
                "sandbox_filename": f.filename,
                "text": "",
                "chars": 0,
                "truncated": False,
                "kind": (meta.get("extract") or {}).get("kind", ""),
                "meta": {"ocr": meta.get("ocr", "unavailable")},
                "processing": (meta.get("extract") or {}).get("coverage", "legacy"),
                "visual": visual_info(f),
                "visual_pages": [],
            }
        )
    return out


def image_reference_for_chat(db, file, *, prepare=True):
    from .routes_library_tools import document_images

    return document_images(db, file, None, prepare=prepare)
