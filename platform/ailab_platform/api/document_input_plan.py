"""Frozen V2 Chat input: bounded native probe, whole-file packing and explicit coverage."""

import json
from datetime import datetime

from fastapi import HTTPException

from ..config import settings
from ..domain.document_input_plan import choose_inline
from ..model_policy import ModelPolicy, estimator
from ..models import FileObject, OutboxMessage, RenderedPage
from . import document_preparation as preparations
from .document_content import manifest as load_manifest, native_text, unit_bytes, visual_info, visual_pages
from .native_preparation import native_kind, native_state, request_native


def processing():
    raise HTTPException(409, {"code": "chat_attachment_processing", "message": "附件正在准备"})


def bounded_job(db, row, job_id):
    deadline = preparations.start_budget(db, row)
    job = db.get(OutboxMessage, job_id) if job_id else None
    if job:
        current = datetime.fromisoformat(job.payload["deadline"])
        job.payload = {**job.payload, "deadline": min(current, deadline).isoformat()}
        db.commit()
    return deadline


def native_document(db, file, preparation):
    if file.content_type.startswith("image/"):
        return None
    if native_kind(file) is None:
        raise HTTPException(422, {"code": "chat_attachment_unsupported", "message": "当前文件格式无法用于对话，请使用工作模式"})
    from .document_text import current_index

    try:
        current_index(db, file)
        available = True
    except HTTPException:
        available = False
    if not available:
        preparations.start_budget(db, preparation)
        state = request_native(db, file, preparation=preparation)
        bounded_job(db, preparation, state.get("job_id"))
        if state["status"] == "failed":
            raise HTTPException(
                422, {"code": "chat_attachment_unavailable", "reason": state.get("reason"), "message": "附件文字准备失败，原件仍保留"}
            )
        processing()
    doc = load_manifest(file)
    if not doc:
        raise HTTPException(409, "native_cache_missing")
    return doc


def native_revision(db, file):
    from .document_text import current_index

    return current_index(db, file).id


def text(file, doc):
    if doc is None:
        return ""
    parts, cache = [], {}
    for unit in doc["units"]:
        parts.append(native_text(file, unit, unit_bytes(file, unit, cache)))
    return "\n\n".join(parts)


def compact_metadata(items, model):
    # A single shared budget covers all attachments. Removing optional labels
    # and outline entries never removes identity or invents document coverage.
    fields = ["file_id", "version", "name", "kind", "bytes", "units", "native", "visual", "locators"]
    rows = [
        [
            item["file_id"],
            item["file_version"],
            item["filename"][:80],
            item["kind"],
            item["size"],
            item["units"],
            item["native_status"],
            item["visual"]["status"],
            item["locators"],
        ]
        for item in items
    ]
    for step in range(4):
        value = json.dumps({"fields": fields, "files": rows}, ensure_ascii=False, separators=(",", ":"))
        if estimator.estimate(model, value) <= 2000:
            return value
        if step == 0:
            for row in rows:
                row[-1] = []
        elif step == 1:
            for row in rows:
                row[2] = row[2][:20]
        elif step == 2:
            for row in rows:
                row[2] = ""
    raise HTTPException(413, {"code": "chat_attachment_navigation_limit", "message": "附件导航超出当前输入预算，请减少附件数量"})


def source_totals(items):
    for field, maximum in (("size", 40 * 1024**2), ("source_chars", 200000), ("source_blocks", 10000), ("cells", 200000)):
        if sum(item[field] for item in items) > maximum:
            raise HTTPException(413, {"code": "chat_attachment_too_large", "message": "附件合计超出对话处理预算"})
    if any(item["size"] > 20 * 1024**2 for item in items):
        raise HTTPException(413, {"code": "chat_attachment_too_large", "message": "单份对话附件不能超过20MiB"})


def prepare(db, user, files, spec, prompt, *, send_id=None):
    if any(file.size > 20 * 1024**2 for file in files) or sum(file.size for file in files) > 40 * 1024**2:
        raise HTTPException(413, {"code": "chat_attachment_too_large", "message": "对话附件超出20/40MiB预算"})
    for file in files:
        limits = (file.meta or {}).get("document_limits") or {}
        if limits.get("version") == file.sha256 and limits.get("reason"):
            raise HTTPException(413, "native_structure_limit")
    preparation = preparations.get_preparation(db, user, files, spec.id, prompt, send_id)
    if preparation.plan:
        validate_frozen(db, files, preparation.plan, spec.id)
        return preparation.plan
    items = []
    bodies = {}
    for ordinal, file in enumerate(files):
        doc = native_document(db, file, preparation)
        is_image = file.content_type.startswith("image/")
        visual = visual_info(file, doc)
        pages = visual_pages(file, doc) if doc else []
        source_chars = doc.get("source_chars", doc["chars"]) if doc else 0
        source_blocks = doc.get("source_blocks", len(doc["units"])) if doc else 0
        if source_chars > 200000 or source_blocks > 10000 or (doc and (doc.get("cells") or 0) > 200000):
            raise HTTPException(413, "native_structure_limit")
        body = text(file, doc) if doc and doc["status"] == "ready" else ""
        bodies[file.id] = body
        images = 1 if is_image else len(pages) if pages is not None else 0
        eligible = (
            spec.supports({"vision"})
            if is_image
            else doc["status"] == "ready"
            and (
                visual["status"] == "none"
                or (spec.supports({"vision"}) and visual["status"] == "present" and pages is not None and len(pages) <= 8)
            )
        )
        items.append(
            {
                "file_id": file.id,
                "source_version": file.sha256,
                "file_version": file.version,
                "filename": file.filename,
                "kind": "image" if is_image else doc["kind"],
                "size": file.size,
                "ordinal": ordinal,
                "eligible": eligible,
                "native_revision": native_revision(db, file) if doc else None,
                "native_status": doc["status"] if doc else "unavailable",
                "source_chars": source_chars,
                "source_blocks": source_blocks,
                "cells": (doc.get("cells") or 0) if doc else 0,
                "units": doc["total_units"] if doc and doc["kind"] in {"pdf", "pptx"} else "unknown",
                "visual": {"status": visual["status"]},
                "locators": [unit["anchor"] for unit in doc["units"][:4]] if doc else [],
                "text_tokens": estimator.estimate(spec.id, body) if body else 0,
                "images": images,
                "document_images": 0 if is_image else images,
                "pages": pages if eligible else [],
                "inline": False,
            }
        )
    source_totals(items)
    selected = choose_inline(items, ModelPolicy.input_budget(spec), settings.vision_input_tokens)
    if len(selected) != len(items) and not spec.supports({"tools"}):
        raise HTTPException(422, {"code": "chat_document_tools_required", "message": "附件需要按需读取，请选择支持工具的模型或缩小文件"})
    for item in items:
        item["inline"] = item["file_id"] in selected
    metadata = compact_metadata(items, spec.id)
    inline_tokens = estimator.estimate(
        spec.id,
        {
            "messages": [
                {"role": "user", "content": prompt + metadata + "\n".join(bodies[item["file_id"]] for item in items if item["inline"])}
            ],
            "tools": [],
        },
    )
    inline_tokens += sum(item["images"] for item in items if item["inline"]) * settings.vision_input_tokens
    if inline_tokens > ModelPolicy.input_budget(spec) // 2:
        # Leave space for actual tools/system/history; context admission still
        # checks the complete real request, including model image limits.
        for item in sorted(items, key=lambda item: (-item["text_tokens"] - item["images"] * settings.vision_input_tokens, item["ordinal"])):
            if not item["inline"]:
                continue
            if not spec.supports({"tools"}):
                raise HTTPException(413, "chat_attachment_too_large")
            item["inline"] = False
            inline_tokens -= item["text_tokens"] + item["images"] * settings.vision_input_tokens
            if inline_tokens <= ModelPolicy.input_budget(spec) // 2:
                break
        if inline_tokens > ModelPolicy.input_budget(spec) // 2:
            raise HTTPException(413, "chat_attachment_too_large")
    # Only selected inline images can start a render/normalization operation.
    for item, file in zip(items, files, strict=True):
        if not item["inline"] or not item["images"]:
            continue
        if item["kind"] == "image":
            if not (file.meta or {}).get("display_key"):
                from .preview_requests import request_preview

                preparations.start_budget(db, preparation)
                preview, _ = request_preview(db, file, preparation=preparation)
                bounded_job(db, preparation, (file.meta or {}).get("preview_job_id"))
                if preview["status"] == "failed":
                    raise HTTPException(422, "attachment_image_unavailable")
                processing()
            from ..storage import get_storage

            item["image_revision"] = (file.meta or {}).get("display_key")
            item["image_digest"] = get_storage().info(item["image_revision"]).etag
        elif item["pages"]:
            from .routes_library_tools import document_images

            binding = document_images(db, file, item["pages"], prepare=False)
            if binding.get("pending"):
                from .routes_ocr import request_page_render

                preparations.start_budget(db, preparation)
                request_page_render(db, file, item["pages"], preparation=preparation)
                bounded_job(db, preparation, (file.meta or {}).get("preview_job_id"))
                processing()
            if binding.get("error") or binding.get("truncated"):
                raise HTTPException(422, "attachment_image_unavailable")
            item["visual_pages"] = binding["files"]
    plan = {
        "version": 2,
        "model": spec.id,
        "preparation_id": preparation.id,
        "metadata": metadata,
        "items": items,
        "budget": {
            "started_at": preparations.aware(preparation.started_at).isoformat(),
            "deadline": preparations.aware(preparation.deadline).isoformat(),
        }
        if preparation.started_at
        else None,
    }
    preparation.plan = plan
    db.commit()
    return plan


def validate_frozen(db, files, plan, model):
    if plan.get("version") != 2 or plan.get("model") != model or [file.id for file in files] != [item["file_id"] for item in plan["items"]]:
        raise HTTPException(409, "document_input_strategy_changed")
    for item, file in zip(plan["items"], files, strict=True):
        if file.deleted_at or file.purged_at or file.sha256 != item["source_version"]:
            raise HTTPException(404, "document_unavailable")
        if item["inline"] and item["kind"] != "image" and native_revision(db, file) != item["native_revision"]:
            raise HTTPException(409, "document_input_strategy_changed")
        if item.get("image_revision") and (file.meta or {}).get("display_key") != item["image_revision"]:
            raise HTTPException(409, "document_input_strategy_changed")
        if item.get("image_revision"):
            from ..storage import get_storage

            if get_storage().info(item["image_revision"]).etag != item["image_digest"]:
                raise HTTPException(409, "document_input_strategy_changed")
        for image in item.get("visual_pages", []):
            page = db.get(RenderedPage, (file.id, image["page"], "poppler-1280-v1"))
            if not page or page.digest != image["digest"]:
                raise HTTPException(409, "document_input_strategy_changed")


def input_files(db, run, refs, *, plan=None):
    plan = plan or (run.options or {}).get("document_input_plan")
    if not plan:
        raise HTTPException(409, "document_input_strategy_missing")
    files = [db.get(FileObject, ref["file_id"]) for ref in refs]
    if any(file is None for file in files):
        raise HTTPException(404, "document_unavailable")
    validate_frozen(db, files, plan, run.model)
    output = []
    for ref, file, item in zip(refs, files, plan["items"], strict=True):
        doc = load_manifest(file) if item["inline"] and item["kind"] != "image" else None
        body = text(file, doc)
        output.append(
            {
                **ref,
                "id": file.id,
                "size": file.size,
                "sandbox_filename": file.filename,
                "text": body,
                "chars": len(body),
                "input_strategy": "prepared" if item["inline"] else "reference",
                "truncated": False,
                "kind": item["kind"],
                "processing": item["native_status"],
                "visual": item["visual"],
                "visual_pages": item.get("visual_pages", []),
                "meta": {"ocr": "not_read"},
                "navigation_text": plan["metadata"] if not output else "",
            }
        )
    return output
