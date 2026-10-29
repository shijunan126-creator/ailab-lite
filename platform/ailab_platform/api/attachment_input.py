"""Deterministic mode-specific input assembly; no untrusted parsing here."""

from fastapi import HTTPException
from sqlalchemy import select

from ..catalog import get_catalog
from ..model_policy import ModelPolicy, estimator
from ..models import FileObject
from ..storage import get_storage
from .document_sources import attachment_manifest

MAX_CHAT_CHARS = 200000


def readable_text(file, limit=MAX_CHAT_CHARS, db=None):
    meta = file.meta or {}
    if meta.get("document_manifest_key"):
        import json

        storage = get_storage()
        manifest = json.loads(storage.get_bytes(meta["document_manifest_key"]))
        from .document_sources import ocr_records

        pages = [unit["unit_number"] - 1 for unit in manifest["units"] if unit.get("quality") == "no_text"]
        if len(pages) > 200:
            return "", False
        records = ocr_records(db, file, pages)
        parts = []
        chars = 0
        for unit in manifest["units"]:
            key = meta["document_manifest_key"].rsplit("/", 1)[0] + "/" + unit["name"]
            if unit.get("quality") == "no_text":
                record = records.get(str(unit["unit_number"] - 1))
                if not record:
                    return "", False
                key = record["text_key"]
            text = storage.get_bytes(key).decode()
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
    text, complete = readable_text(file, db=db)
    if not complete:
        raise HTTPException(422, {"code": "chat_attachment_unavailable", "message": "文件文字尚不可读，请稍候或切换工作模式"})
    return text


def validate_chat_files(db, files, model, prompt):
    spec = get_catalog().get(model)
    if not spec:
        return
    texts = []
    for fid in files:
        file = db.get(FileObject, fid)
        meta = file.meta or {}
        pages = None
        needs_ocr = (file.content_type or "").startswith("image/") and not spec.supports({"vision"}) and meta.get("ocr") != "done"
        if meta.get("document_manifest_key"):
            import json

            manifest = json.loads(get_storage().get_bytes(meta["document_manifest_key"]))
            from .document_sources import ocr_records

            needed = [unit["unit_number"] - 1 for unit in manifest["units"] if unit.get("quality") == "no_text"]
            if len(needed) > 200:
                raise HTTPException(422, {"code": "chat_attachment_too_large", "message": "扫描页超出对话准备范围，请切换工作模式"})
            records = ocr_records(db, file, needed)
            pages = list(
                dict.fromkeys(
                    unit["unit_number"]
                    for unit in manifest["units"]
                    if unit.get("quality") == "no_text" and str(unit["unit_number"] - 1) not in records
                )
            )[:20]
            needs_ocr = bool(pages)
        if needs_ocr:
            from .routes_ocr import request_extraction

            request_extraction(db, file, pages)
            db.commit()
            raise HTTPException(409, {"code": "chat_attachment_processing", "message": "附件正在提取文字，请稍候再发送"})
        texts.append(chat_file(file, spec, db))
    # Reserve half the input budget for system instructions, tools and history.
    if estimator.estimate(model, {"messages": [{"role": "user", "content": prompt + "\\n".join(texts)}], "tools": []}) > (
        ModelPolicy.input_budget(spec) // 2
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
            }
        )
    return out
