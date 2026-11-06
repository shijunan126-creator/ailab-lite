"""Native scope identity and budgets. Reading available text never starts new work."""

from fastapi import HTTPException
from sqlalchemy import select

from ..contracts.document_input import Pages, Sheet, Word
from ..models import DocumentBlock, DocumentNativeRange, FileDerivation
from ..native_text import PARSER_NATIVE
from . import document_operations as operations
from .document_ocr import scope_key
from .jobs import stage_job


def scope_query(file):
    from .native_preparation import native_state

    return (
        select(DocumentNativeRange)
        .join(FileDerivation, FileDerivation.id == DocumentNativeRange.derivation_id)
        .where(
            DocumentNativeRange.file_id == file.id,
            DocumentNativeRange.source_version == file.sha256,
            DocumentNativeRange.parser == PARSER_NATIVE,
            DocumentNativeRange.base_derivation_id == native_state(file).get("derivation_id"),
            FileDerivation.status.in_(["ready", "partial"]),
        )
    )


def cached_scope(db, file, selector):
    row = db.scalar(scope_query(file).where(DocumentNativeRange.selector_key == scope_key(selector)).limit(1))
    return row


def missing_scope(db, file, index, selector):
    if isinstance(selector, Pages):
        kind = ((file.meta or {}).get("extract") or {}).get("kind")
        if kind not in {"pdf", "pptx"}:
            raise HTTPException(422, "selector_representation_mismatch")
        total = ((file.meta or {}).get("extract") or {}).get("units")
        if type(total) is int and any(page > total for page in selector.pages):
            raise HTTPException(422, "document_page_out_of_range")
        found = set(
            db.scalars(
                select(DocumentBlock.unit_number)
                .where(DocumentBlock.derivation_id == index.id, DocumentBlock.unit_number.in_(selector.pages))
                .distinct()
                .limit(20)
            )
        )
        found.update(
            db.scalars(scope_query(file).where(DocumentNativeRange.page.in_(selector.pages)).with_only_columns(DocumentNativeRange.page))
        )
        missing = [page for page in selector.pages if page not in found]
        return {"kind": "pages", "pages": missing} if missing else None
    if isinstance(selector, Word) and "/" in selector.anchor:
        if ((file.meta or {}).get("extract") or {}).get("kind") != "docx":
            raise HTTPException(422, "selector_representation_mismatch")
        scope = selector.model_dump(mode="json", exclude_none=True)
        return None if cached_scope(db, file, scope) else scope
    if isinstance(selector, Sheet) and selector.range:
        if ((file.meta or {}).get("extract") or {}).get("kind") != "xlsx":
            raise HTTPException(422, "selector_representation_mismatch")
        scope = selector.model_dump(mode="json", exclude_none=True)
        return None if cached_scope(db, file, scope) else scope
    return None


def request_scope(db, run, file, index, request):
    missing = missing_scope(db, file, index, request.selector)
    if not missing:
        return None
    if request.cursor:
        raise HTTPException(409, "cursor_stale")
    selector = request.selector.model_dump(mode="json", exclude_none=True)
    operation = operations.request(
        db,
        run,
        lane="render",
        kind="native_scope",
        file_id=file.id,
        version=file.sha256,
        binding={
            "selector": selector,
            "identity": {"selector": selector, "parser": PARSER_NATIVE, "base_derivation_id": index.id},
            "base_derivation_id": index.id,
        },
        priority=1,
        share=True,
    )
    if operation.status in {"failed", "canceled"}:
        return {"file_id": file.id, "version": file.sha256, "error": (operation.result or {}).get("reason", "native_scope_failed")}
    if not operation.job_id:
        operation.job_id = operation.id
        job = stage_job(db, "native_scope", file_id=file.id, job_id=operation.id, priority=True)
        job.payload = {**job.payload, "deadline": operations.aware(operation.deadline).isoformat()}
    return {
        "file_id": file.id,
        "version": file.sha256,
        "pending": True,
        "action": "native-scope",
        "operation_id": operation.id,
        "deadline": min(operations.budget(run), operations.aware(operation.deadline)).timestamp(),
        "pages": selector.get("pages"),
    }


def charge_consumption(db, run, file):
    """Full source size and all known source facts count even for a small scope."""
    from .native_preparation import native_state

    extract = (file.meta or {}).get("extract") or {}
    ranges = (file.meta or {}).get("native_range_budget") or {}
    if ranges.get("base_derivation_id") != native_state(file).get("derivation_id"):
        ranges = {}
    facts = {
        "version": file.sha256,
        "bytes": file.size,
        "chars": max(extract.get("source_chars", extract.get("chars", 0)), ranges.get("chars", 0)),
        "blocks": max(extract.get("source_blocks", 0), ranges.get("blocks", 0)),
        "cells": extract.get("cells") or 0,
    }
    if not facts["blocks"] and native_state(file).get("derivation_id"):
        from sqlalchemy import func

        facts["blocks"] = db.scalar(
            select(func.count()).select_from(DocumentBlock).where(DocumentBlock.derivation_id == native_state(file)["derivation_id"])
        )
    known = dict((run.runtime_meta or {}).get("document_inputs") or {})
    known[file.id] = facts
    if len(known) > 2048:
        raise HTTPException(413, "document_source_limit")
    if file.size > 20 * 1024**2 or sum(item["bytes"] for item in known.values()) > 40 * 1024**2:
        raise HTTPException(413, "document_input_limit")
    for field, maximum in (("blocks", 10000), ("chars", 200000), ("cells", 200000)):
        if sum(item[field] for item in known.values()) > maximum:
            raise HTTPException(413, "document_structure_limit")
    run.runtime_meta = {**(run.runtime_meta or {}), "document_inputs": known}
