"""Deterministic mode-specific input assembly; no untrusted parsing here."""

from fastapi import HTTPException
from sqlalchemy import select

from ..catalog import get_catalog
from ..models import FileObject
from .document_content import visual_info
from .document_sources import attachment_manifest


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
                "kind": (meta.get("extract") or {}).get("kind", ""),
                "meta": {"ocr": meta.get("ocr", "unavailable")},
                "processing": (meta.get("extract") or {}).get("coverage", "legacy"),
                "visual": visual_info(f),
            }
        )
    return out
