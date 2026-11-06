"""Authorized-source text operations; transport and run/catalog admission are separate."""

import hashlib
import hmac

from fastapi import HTTPException
from sqlalchemy import select

from ..config import settings
from ..contracts.document_input import Pages, ReadDocument, Region, SearchDocument, Sheet, Word
from ..domain.document_reading import (
    CursorError,
    TextBlock,
    context_digest,
    decode_cursor,
    encode_cursor,
    read_batch,
    search_batch,
)
from ..models import DerivedAsset, DocumentBlock, FileDerivation, OcrPageResult
from ..native_text import NativeCacheError, read_unit
from ..storage import get_storage
from .native_preparation import native_state


def current_index(db, file):
    state = native_state(file)
    # Reuse a previously indexed immutable legacy generation without parsing it
    # again. Publication owns this pointer; retired indexes are never selected.
    if state.get("status") == "absent":
        key = (file.meta or {}).get("document_manifest_key") or ((file.meta or {}).get("extract") or {}).get("document_manifest_key")
        if key:
            index = db.scalar(
                select(FileDerivation).where(
                    FileDerivation.file_id == file.id,
                    FileDerivation.kind == "native",
                    FileDerivation.manifest_key == key,
                    FileDerivation.status.in_(["ready", "partial"]),
                )
            )
            if index:
                return index
    if state.get("version") != file.sha256 or state.get("status") != "ready":
        raise HTTPException(409, "native_representation_unavailable")
    index = db.get(FileDerivation, state.get("derivation_id"))
    if not index or index.file_id != file.id or index.status not in {"ready", "partial"}:
        raise HTTPException(409, "native_representation_unavailable")
    return index


def select_blocks(file, index, selector, position):
    q = select(DocumentBlock).where(DocumentBlock.derivation_id == index.id, DocumentBlock.ordinal >= position.ordinal)
    kind = (file.meta.get("extract") or {}).get("kind")
    if isinstance(selector, Pages):
        if kind not in {"pdf", "pptx"}:
            raise HTTPException(422, "selector_representation_mismatch")
        total = (file.meta.get("extract") or {}).get("units")
        if type(total) is int and any(page > total for page in selector.pages):
            raise HTTPException(422, "document_page_out_of_range")
        q = q.where(DocumentBlock.unit_number.in_(selector.pages))
    elif isinstance(selector, Word):
        if kind != "docx":
            raise HTTPException(422, "selector_representation_mismatch")
        if "/" in selector.anchor:
            raise HTTPException(422, "selector_requires_scoped_preparation")
        q = q.where(DocumentBlock.anchor == selector.anchor)
    elif isinstance(selector, Sheet):
        if kind != "xlsx":
            raise HTTPException(422, "selector_representation_mismatch")
        if selector.range:
            raise HTTPException(422, "selector_requires_scoped_preparation")
        # The runtime's formatSheet emits either a plain name or Excel quoting.
        quoted = "'" + selector.sheet.replace("'", "''") + "'"
        q = q.where(DocumentBlock.anchor.in_([selector.sheet, quoted]))
    return q.order_by(DocumentBlock.ordinal).limit(101)


# The native contract has at most 10k blocks. The separate namespace keeps
# physical OCR page ids and intra-block offsets stable without materializing a
# merged index or renumbering earlier native blocks when OCR is added.
OCR_BASE = 2**31


def ocr_query(file, selector, ordinal):
    query = select(OcrPageResult).where(OcrPageResult.file_id == file.id, OcrPageResult.page >= max(0, ordinal - OCR_BASE))
    if settings.ocr_engine_fingerprint:
        query = query.where(OcrPageResult.fingerprint == settings.ocr_engine_fingerprint)
    else:
        generation = (file.meta or {}).get("ocr_generation")
        if not generation:
            return None
        query = query.where(OcrPageResult.generation == generation)
    if isinstance(selector, Pages):
        query = query.where(OcrPageResult.page.in_([page - 1 for page in selector.pages]))
    elif selector is not None:
        # A whole-page legacy OCR result cannot stand in for an explicit region
        # or for Word/Excel structure whose physical-page mapping is unknown.
        return None
    return query.order_by(OcrPageResult.page).limit(101)


def available_ocr(db, file):
    query = ocr_query(file, None, OCR_BASE)
    return bool(query is not None and db.scalar(query.with_only_columns(OcrPageResult.page).limit(1)) is not None)


def ocr_text(storage, row, file):
    if not row.text_key.startswith(f"derived/{file.id}/ocr/") or ".." in row.text_key.split("/"):
        raise NativeCacheError("invalid_ocr_binding")
    data = bytearray()
    with storage.open_stream(row.text_key) as chunks:
        for chunk in chunks:
            if len(data) + len(chunk) > 800000:
                raise NativeCacheError("ocr_text_limit")
            data.extend(chunk)
    # New writers carry an integrity digest in the asset ledger. Legacy OCR
    # rows retain their explicit legacy provenance rather than invented hashes.
    return data


def text_operation(db, file, request: ReadDocument | SearchDocument):
    """Consumes an already authorized immutable file; never starts parsing or OCR.

    New-run transport owns permission/attempt admission and pending preparation;
    legacy tools retain their separately frozen contracts.
    """
    searching = isinstance(request, SearchDocument)
    navigation = not searching and request.navigation_only
    reading_ocr = not searching and request.text_source == "ocr"
    if reading_ocr and isinstance(request.selector, Region):
        raise HTTPException(409, "ocr_representation_unavailable")
    index = None
    if not reading_ocr:
        try:
            index = current_index(db, file)
        except HTTPException:
            if not searching or not available_ocr(db, file):
                raise
    selector = request.selector.model_dump(mode="json") if request.selector else None
    context = context_digest(
        operation="search" if searching else ("navigate" if navigation else "read"),
        file_id=file.id,
        version=file.sha256,
        representation=(
            [index.id if index else None, (file.meta or {}).get("ocr_cache_revision", 0), settings.ocr_engine_fingerprint]
            if searching
            else ["ocr", (file.meta or {}).get("ocr_cache_revision", 0), settings.ocr_engine_fingerprint]
            if reading_ocr
            else index.id
        ),
        selector=selector,
        query=request.query if searching else None,
    )
    key = hmac.digest(settings.caller_token("api").encode(), b"document-cursor-v2", "sha256")
    try:
        position = decode_cursor(request.cursor, context, key=key)
        native_scope_mapped = not (
            searching
            and isinstance(request.selector, Pages)
            and ((file.meta or {}).get("extract") or {}).get("kind") not in {"pdf", "pptx"}
        )
        rows = (
            db.scalars(select_blocks(file, index, request.selector, position)).all()
            if index and position.ordinal < OCR_BASE and not reading_ocr and native_scope_mapped
            else []
        )
        ocr_rows = []
        if (searching or reading_ocr) and len(rows) <= 100:
            query = ocr_query(file, request.selector, position.ordinal)
            ocr_rows = db.scalars(query).all() if query is not None else []
        if reading_ocr and not ocr_rows:
            raise HTTPException(409, "ocr_representation_unavailable")
        storage, cache = get_storage(), {}
        scanned = {"native": 0, "ocr": 0}
        ocr_digests = (
            dict(
                db.execute(
                    select(DerivedAsset.key, DerivedAsset.digest).where(
                        DerivedAsset.key.in_([row.text_key for row in ocr_rows]), DerivedAsset.state == "ready"
                    )
                ).all()
            )
            if ocr_rows
            else {}
        )

        def blocks():
            # Fetch 101 metadata rows for lookahead, but decode only the 100
            # candidate blocks. The lookahead body cannot affect this result.
            for i, row in enumerate(rows):
                text = ""
                if i < 100:
                    text = read_unit(
                        storage,
                        row.text_key,
                        {"byte_offset": row.byte_offset, "byte_size": row.byte_size, "digest": row.digest},
                        cache,
                    ).decode("utf-8")
                if i < 100:
                    scanned["native"] += 1
                yield TextBlock(row.ordinal, row.anchor, text, block_id=row.id)
            for i, row in enumerate(ocr_rows):
                text = ""
                if i + len(rows) < 100:
                    data = ocr_text(storage, row, file)
                    expected = ocr_digests.get(row.text_key)
                    if expected and len(expected) == 64 and hashlib.sha256(data).hexdigest() != expected:
                        raise NativeCacheError("ocr_cache_changed")
                    text = data.decode("utf-8")
                if i + len(rows) < 100:
                    scanned["ocr"] += 1
                yield TextBlock(OCR_BASE + row.page, f"page:{row.page + 1}", text, source="ocr")

        if navigation:
            if position.offset:
                raise CursorError("invalid_cursor")
            from ..domain.document_reading import Position

            result = {
                "navigation": [
                    {"block_id": row.id, "locator": row.anchor, "unit": row.unit_number, "chars": row.chars} for row in rows[:100]
                ],
                "checked_blocks": min(100, len(rows)),
                "next_position": Position(rows[100].ordinal) if len(rows) > 100 else None,
                "coverage": [],
            }
        else:
            result = search_batch(blocks(), request.query, position) if searching else read_batch(blocks(), position)
    except CursorError as exc:
        raise HTTPException(409, str(exc)) from None
    except (NativeCacheError, UnicodeError) as exc:
        raise HTTPException(409, "native_cache_changed") from exc
    except FileNotFoundError as exc:
        raise HTTPException(409, "native_cache_missing") from exc
    next_position = result.pop("next_position")
    return {
        "file_id": file.id,
        "version": file.sha256,
        "representation": {
            "kind": "available_text" if searching else "ocr" if reading_ocr else "native",
            "revision": index.id if index else str((file.meta or {}).get("ocr_cache_revision", 0)),
            "parser": index.parser_version if index else None,
            "ocr_provenance": "legacy_page_cache" if ocr_rows else None,
        },
        **result,
        "next_cursor": encode_cursor(context, next_position, key=key) if next_position is not None else None,
        "truncated": next_position is not None,
        "navigation_status": "available_index" if navigation else "not_requested",
        "physical_page_count": ((file.meta or {}).get("extract") or {}).get("units")
        if ((file.meta or {}).get("extract") or {}).get("kind") == "pdf"
        else "unknown",
        "coverage": {
            "native": {
                "indexed_status": index.status if index else "unavailable",
                "scanned_blocks": scanned["native"],
                "selector_mapping": "available" if native_scope_mapped else "unknown",
                "end_of_available_text": next_position is None or next_position.ordinal >= OCR_BASE,
            },
            "visual": {"status": "not_viewed"},
            "ocr": {
                "status": ("searched_available" if searching else "read_selected")
                if scanned["ocr"]
                else "not_searched"
                if searching
                else "not_read",
                "scanned_blocks": scanned["ocr"],
                "end_of_available_text": next_position is None,
                "unprepared_pages_or_regions": "not_covered",
            },
            "scope": selector or {"kind": "available_text" if searching else "available_native_text"},
            "blocks": result.get("coverage", []),
        },
    }
