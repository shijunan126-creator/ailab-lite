"""Deterministic mode-specific input assembly; no untrusted parsing here."""

from fastapi import HTTPException
from sqlalchemy import select

from ..catalog import get_catalog
from ..config import settings
from ..model_policy import ModelPolicy, estimator
from ..models import FileObject
from ..storage import get_storage
from .document_content import visual_info
from .document_sources import attachment_manifest

MAX_CHAT_CHARS = 200000


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
        parts, chars = [], 0
        for unit in manifest["units"]:
            key = meta["document_manifest_key"].rsplit("/", 1)[0] + "/" + unit["name"]
            raw = storage.get_bytes(key)
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
        parts, chars = [], 0
        from .document_content import native_text

        prefix = meta["document_manifest_key"].rsplit("/", 1)[0] + "/"
        for unit in doc["units"]:
            text = native_text(file, unit, get_storage().get_bytes(prefix + unit["name"]))
            chars += len(text)
            if chars > MAX_CHAT_CHARS:
                _too_large()
            parts.append(text)
        return "\n\n".join(parts)
    text, complete = readable_text(file, db=db)
    if not complete:
        raise HTTPException(422, {"code": "chat_attachment_unavailable", "message": "文件文字尚不可读，请稍候或切换工作模式"})
    return text


def validate_chat_files(db, files, model, prompt):
    spec = get_catalog().get(model)
    if not spec:
        return
    texts = []
    visual_count = 0
    for fid in files:
        file = db.get(FileObject, fid)
        meta = file.meta or {}
        pages = None
        needs_ocr = (file.content_type or "").startswith("image/") and not spec.supports({"vision"}) and meta.get("ocr") != "done"
        if meta.get("document_manifest_key"):
            import json

            manifest = json.loads(get_storage().get_bytes(meta["document_manifest_key"]))
            from .document_content import native_text, visual_info
            from .document_sources import ocr_records

            needed = []
            prefix = meta["document_manifest_key"].rsplit("/", 1)[0] + "/"
            for unit in manifest["units"]:
                if not unit["anchor"].startswith("page:") or not unit.get("visual", True):
                    continue
                missing_text = unit.get("quality") == "no_text"
                if manifest.get("parser") == "zcode-3c5b227-ailab1":
                    missing_text = not native_text(file, unit, get_storage().get_bytes(prefix + unit["name"])).strip()
                if missing_text:
                    needed.append(unit["unit_number"] - 1)
            visual = visual_info(file, manifest)
            if spec.supports({"vision"}) and visual["status"] != "none":
                binding = image_reference_for_chat(db, file)
                if binding.get("pending"):
                    db.commit()
                    raise HTTPException(409, {"code": "chat_attachment_processing", "message": "附件正在准备视觉页面"})
                if binding.get("error"):
                    raise HTTPException(422, {"code": "chat_attachment_unavailable", "message": binding["error"]})
                if binding.get("truncated"):
                    _too_large()
                visual_count += len(binding.get("files", []))
                needed = []
            if len(needed) > 200:
                raise HTTPException(422, {"code": "chat_attachment_too_large", "message": "扫描页超出对话准备范围，请切换工作模式"})
            records = ocr_records(db, file, needed)
            pages = list(
                dict.fromkeys(
                    unit["unit_number"]
                    for unit in manifest["units"]
                    if unit["unit_number"] - 1 in needed and str(unit["unit_number"] - 1) not in records
                )
            )[:20]
            needs_ocr = bool(pages)
        if needs_ocr:
            from .routes_ocr import request_extraction

            request_extraction(db, file, pages)
            db.commit()
            raise HTTPException(409, {"code": "chat_attachment_processing", "message": "附件正在提取文字，请稍候再发送"})
        texts.append(chat_file(file, spec, db))
        if not spec.supports({"vision"}) and meta.get("document_manifest_key"):
            from .document_content import visual_info

            if visual_info(file)["status"] != "none" and not texts[-1].strip():
                raise HTTPException(
                    422, {"code": "visual_model_required", "message": "文件只有图片或图形，当前模型无法理解，请选择视觉模型"}
                )
    # Reserve half the input budget for system instructions, tools and history.
    if estimator.estimate(model, {"messages": [{"role": "user", "content": prompt + "\\n".join(texts)}], "tools": []}) > (
        ModelPolicy.input_budget(spec) // 2 - visual_count * settings.vision_input_tokens
    ):
        raise HTTPException(422, {"code": "chat_attachment_too_large", "message": "附件超出当前模型输入预算，请选择范围或切换工作模式"})


def input_files(db, run, **message):
    refs = attachment_manifest(db, run, **message)
    spec = get_catalog().get(run.model)
    chat = (run.options or {}).get("mode", "work") == "chat"
    files = {f.id: f for f in db.scalars(select(FileObject).where(FileObject.id.in_([r["file_id"] for r in refs])))}
    out = []
    for ref in refs:
        f = files[ref["file_id"]]
        meta = f.meta or {}
        image = (f.content_type or "").startswith("image/")
        text = chat_file(f, spec, db) if spec and (chat or (image and not spec.supports({"vision"}))) else ""
        visual_pages = []
        if chat and spec and spec.supports({"vision"}) and meta.get("document_manifest_key"):
            binding = image_reference_for_chat(db, f, prepare=False)
            if binding.get("pending") or binding.get("error") or binding.get("truncated"):
                raise HTTPException(422, {"code": "chat_attachment_unavailable", "message": "附件视觉页面尚不可读，请重新准备"})
            visual_pages = binding.get("files", [])
        out.append(
            {
                **ref,
                "id": f.id,
                "input_strategy": "prepared" if chat or image else "reference",
                "sandbox_filename": f.filename,
                "text": text,
                "chars": len(text),
                "truncated": False,
                "kind": (meta.get("extract") or {}).get("kind", ""),
                "meta": {"ocr": meta.get("ocr", "unavailable")},
                "processing": (meta.get("extract") or {}).get("coverage", "legacy"),
                "visual": visual_info(f),
                "visual_pages": visual_pages,
            }
        )
    return out


def image_reference_for_chat(db, file, *, prepare=True):
    from .routes_library_tools import document_images

    return document_images(db, file, None, prepare=prepare)
