"""Authorized-source text operations; transport and run/catalog admission are separate."""

import hashlib
import hmac

from fastapi import HTTPException
from sqlalchemy import select

from ..config import settings
from ..contracts.document_input import Pages, ReadDocument, Region, SearchDocument, Sheet, Word
from ..domain.document_reading import (
    MAX_BLOCKS,
    SEARCH_BLOCKS,
    CursorError,
    TextBlock,
    context_digest,
    decode_cursor,
    encode_cursor,
    read_batch,
    search_batch,
)
from ..models import DerivedAsset, DocumentBlock, DocumentOcrUnit, FileDerivation, OcrPageResult
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


# Storage objects one call may open: a canonical text buffer serves all of its
# blocks, while each legacy block and OCR page is its own object.
MAX_OBJECTS = 100


def select_blocks(file, index, selector, position, window=MAX_BLOCKS):
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
        if "/" in selector.anchor and index.kind != "native-scope":
            raise HTTPException(422, "selector_requires_scoped_preparation")
        q = q.where(DocumentBlock.anchor == selector.anchor)
    elif isinstance(selector, Sheet):
        if kind != "xlsx":
            raise HTTPException(422, "selector_representation_mismatch")
        if selector.range and index.kind != "native-scope":
            raise HTTPException(422, "selector_requires_scoped_preparation")
        # The runtime's formatSheet emits either a plain name or Excel quoting.
        quoted = "'" + selector.sheet.replace("'", "''") + "'"
        anchors = [selector.sheet, quoted]
        if selector.range:
            anchors = [anchor + "!" + selector.range for anchor in anchors]
        q = q.where(DocumentBlock.anchor.in_(anchors))
    return q.order_by(DocumentBlock.ordinal).limit(window + 1)


def native_blocks(db, file, index, selector, position, window=MAX_BLOCKS):
    rows = list(db.scalars(select_blocks(file, index, selector, position, window)))
    if ((file.meta or {}).get("extract") or {}).get("kind") != "pdf" or index.kind != "native":
        return rows
    from types import SimpleNamespace

    from .native_ranges import scope_query

    scopes = scope_query(file).subquery()
    ordinal = 100000 + scopes.c.ordinal * 10001 + DocumentBlock.ordinal
    query = (
        select(DocumentBlock, ordinal)
        .join(scopes, (scopes.c.derivation_id == DocumentBlock.derivation_id) & (scopes.c.page == DocumentBlock.unit_number))
        .where(ordinal >= position.ordinal)
    )
    if isinstance(selector, Pages):
        query = query.where(DocumentBlock.unit_number.in_(selector.pages))
    elif selector is not None:
        return rows
    for row, global_ordinal in db.execute(query.order_by(scopes.c.ordinal, DocumentBlock.ordinal).limit(window + 1)):
        rows.append(
            SimpleNamespace(
                **{
                    field: getattr(row, field)
                    for field in ("id", "anchor", "unit_number", "chars", "text_key", "byte_offset", "byte_size", "digest")
                },
                ordinal=global_ordinal,
            )
        )
    return sorted(rows, key=lambda row: row.ordinal)[: window + 1]


def page_navigation(db, file, index, request):
    """Stable page locators need PDF metadata, never all page bodies/operators."""
    from ..domain.document_reading import Position
    from ..models import DocumentNativeRange
    from .native_ranges import scope_query

    total = ((file.meta or {}).get("extract") or {}).get("units")
    if type(total) is not int or total < 0:
        raise HTTPException(409, "native_page_count_unavailable")
    context = context_digest(
        operation="native_page_navigation",
        file_id=file.id,
        version=file.sha256,
        representation=[index.id, (file.meta or {}).get("native_range_revision", 0)],
    )
    key = hmac.digest(settings.caller_token("api").encode(), b"document-cursor-v2", "sha256")
    try:
        position = decode_cursor(request.cursor, context, key=key)
        if position.offset or position.ordinal > total:
            raise CursorError("invalid_cursor")
    except CursorError as exc:
        raise HTTPException(409, str(exc)) from None
    pages = list(range(position.ordinal + 1, min(total, position.ordinal + 100) + 1))
    prepared = set(
        db.scalars(
            select(DocumentBlock.unit_number)
            .where(DocumentBlock.derivation_id == index.id, DocumentBlock.unit_number.in_(pages))
            .distinct()
            .limit(100)
        )
    )
    prepared.update(
        db.scalars(scope_query(file).where(DocumentNativeRange.page.in_(pages)).with_only_columns(DocumentNativeRange.page).limit(100))
    )
    next_position = Position(pages[-1]) if pages and pages[-1] < total else None
    return {
        "file_id": file.id,
        "version": file.sha256,
        "representation": {"kind": "native", "revision": index.id},
        "navigation": [
            {"locator": f"page:{page}", "unit": page, "native": "prepared" if page in prepared else "unknown", "visual": "unknown"}
            for page in pages
        ],
        "physical_page_count": total,
        "navigation_status": "physical_locators",
        "next_cursor": encode_cursor(context, next_position, key=key) if next_position else None,
        "truncated": next_position is not None,
        "coverage": {"native": {"indexed_status": index.status}, "visual": {"status": "not_viewed"}, "ocr": {"status": "not_read"}},
        "text": "",
    }


# The native contract has at most 10k blocks. The separate namespace keeps
# physical OCR page ids and intra-block offsets stable without materializing a
# merged index or renumbering earlier native blocks when OCR is added.
OCR_BASE = 2**31
OCR_SCOPES_BASE = 2**40


def ocr_query(file, selector, ordinal, window=MAX_BLOCKS):
    adopted = (
        select(DocumentOcrUnit.id)
        .where(
            DocumentOcrUnit.file_id == file.id,
            DocumentOcrUnit.source_version == file.sha256,
            DocumentOcrUnit.fingerprint == OcrPageResult.fingerprint,
            DocumentOcrUnit.page == OcrPageResult.page + 1,
            DocumentOcrUnit.selector["kind"].as_string() == "pages",
        )
        .exists()
    )
    query = select(OcrPageResult).where(OcrPageResult.file_id == file.id, OcrPageResult.page >= max(0, ordinal - OCR_BASE), ~adopted)
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
    return query.order_by(OcrPageResult.page).limit(window + 1)


def scoped_ocr_query(file, selector, ordinal, window=MAX_BLOCKS):
    if not settings.ocr_engine_fingerprint:
        return None
    query = select(DocumentOcrUnit).where(
        DocumentOcrUnit.file_id == file.id,
        DocumentOcrUnit.source_version == file.sha256,
        DocumentOcrUnit.fingerprint == settings.ocr_engine_fingerprint,
        DocumentOcrUnit.ordinal >= max(0, ordinal - OCR_SCOPES_BASE),
    )
    if isinstance(selector, Pages):
        query = query.where(DocumentOcrUnit.page.in_(selector.pages))
    elif isinstance(selector, Region):
        from .document_ocr import scope_key

        query = query.where(DocumentOcrUnit.selector_key == scope_key(selector.model_dump(mode="json", exclude_none=True)))
    elif selector is not None:
        return None
    return query.order_by(DocumentOcrUnit.ordinal).limit(window + 1)


def available_ocr(db, file):
    scoped = scoped_ocr_query(file, None, OCR_SCOPES_BASE)
    if scoped is not None and db.scalar(scoped.with_only_columns(DocumentOcrUnit.id).limit(1)) is not None:
        return True
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
    if (
        index
        and not searching
        and not reading_ocr
        and request.selector is None
        and ((file.meta or {}).get("extract") or {}).get("kind") == "pdf"
        and (navigation or index.status == "partial")
    ):
        return page_navigation(db, file, index, request)
    if index and isinstance(request.selector, (Word, Sheet)):
        needs_scope = (isinstance(request.selector, Word) and "/" in request.selector.anchor) or (
            isinstance(request.selector, Sheet) and request.selector.range
        )
        if needs_scope:
            from .native_ranges import cached_scope

            scope = cached_scope(db, file, request.selector.model_dump(mode="json", exclude_none=True))
            if scope:
                index = db.get(FileDerivation, scope.derivation_id)
            elif searching:
                # Search does not create a new projection just to inspect it.
                return {
                    "file_id": file.id,
                    "version": file.sha256,
                    "hits": [],
                    "truncated": False,
                    "next_cursor": None,
                    "coverage": {"native": {"status": "scope_not_prepared"}, "ocr": {"status": "scope_not_mapped"}},
                }
    selector = request.selector.model_dump(mode="json") if request.selector else None
    context = context_digest(
        operation="search" if searching else ("navigate" if navigation else "read"),
        file_id=file.id,
        version=file.sha256,
        representation=(
            [
                index.id if index else None,
                (file.meta or {}).get("native_range_revision", 0),
                (file.meta or {}).get("ocr_cache_revision", 0),
                settings.ocr_engine_fingerprint,
            ]
            if searching
            else ["ocr", (file.meta or {}).get("ocr_cache_revision", 0), settings.ocr_engine_fingerprint]
            if reading_ocr
            else [index.id, (file.meta or {}).get("native_range_revision", 0)]
        ),
        selector=selector,
        query=request.query if searching else None,
    )
    key = hmac.digest(settings.caller_token("api").encode(), b"document-cursor-v2", "sha256")
    try:
        position = decode_cursor(request.cursor, context, key=key)
        native_scope_mapped = not (
            searching
            and (
                isinstance(request.selector, Region)
                or (isinstance(request.selector, Pages) and ((file.meta or {}).get("extract") or {}).get("kind") not in {"pdf", "pptx"})
            )
        )
        window = SEARCH_BLOCKS if searching else MAX_BLOCKS
        # Each OCR result is its own storage object, so rows past the object
        # budget could never load in this call.
        ocr_window = min(window, MAX_OBJECTS)
        rows = (
            native_blocks(db, file, index, request.selector, position, window)
            if index and position.ordinal < OCR_BASE and not reading_ocr and native_scope_mapped
            else []
        )
        ocr_rows = []
        if (searching or reading_ocr) and len(rows) <= window and position.ordinal < OCR_SCOPES_BASE:
            query = ocr_query(file, request.selector, position.ordinal, ocr_window)
            ocr_rows = db.scalars(query).all() if query is not None else []
        if reading_ocr and (
            not ocr_rows or (isinstance(request.selector, Pages) and {row.page + 1 for row in ocr_rows} != set(request.selector.pages))
        ):
            raise HTTPException(409, "ocr_representation_unavailable")
        scoped_rows = []
        if searching and len(rows) + len(ocr_rows) <= window:
            query = scoped_ocr_query(file, request.selector, position.ordinal, ocr_window)
            scoped_rows = db.scalars(query).all() if query is not None else []
        storage, cache = get_storage(), {}
        sources = []  # source of each loaded block, in order
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
            # Metadata rows include one lookahead; a block is decoded only while
            # this call's block and storage-object budgets allow. The first
            # block it does not load ends the batch and becomes the cursor.
            opened, loaded = set(), 0

            def admit(key):
                nonlocal loaded
                if loaded >= window or (key not in opened and len(opened) >= MAX_OBJECTS):
                    return False
                opened.add(key)
                loaded += 1
                return True

            for row in rows:
                if not admit(row.text_key):
                    yield TextBlock(row.ordinal, row.anchor, None, block_id=row.id)
                    return
                text = read_unit(
                    storage,
                    row.text_key,
                    {"byte_offset": row.byte_offset, "byte_size": row.byte_size, "digest": row.digest},
                    cache,
                ).decode("utf-8")
                sources.append("native")
                yield TextBlock(row.ordinal, row.anchor, text, block_id=row.id)
            for row in ocr_rows:
                if not admit(row.text_key):
                    yield TextBlock(OCR_BASE + row.page, f"page:{row.page + 1}", None, source="ocr")
                    return
                data = ocr_text(storage, row, file)
                expected = ocr_digests.get(row.text_key)
                if expected and len(expected) == 64 and hashlib.sha256(data).hexdigest() != expected:
                    raise NativeCacheError("ocr_cache_changed")
                sources.append("ocr")
                yield TextBlock(OCR_BASE + row.page, f"page:{row.page + 1}", data.decode("utf-8"), source="ocr")
            for row in scoped_rows:
                locator = f"page:{row.page}" if row.page else "image:" + row.selector.get("image_id", row.id)
                if row.selector.get("kind") == "region":
                    locator += "/region:" + row.selector_key[:16]
                identity = {
                    "source": "ocr",
                    "block_id": row.id,
                    "selector": row.selector,
                    "provenance": {"source": row.source_kind, "revision": row.image_revision, **(row.details or {})},
                }
                if not admit(("scoped", row.id)):
                    yield TextBlock(OCR_SCOPES_BASE + row.ordinal, locator, None, **identity)
                    return
                from .document_ocr import text_bytes

                sources.append("ocr")
                yield TextBlock(OCR_SCOPES_BASE + row.ordinal, locator, text_bytes(storage, row).decode("utf-8"), **identity)

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
    # A batch may load one block it then stops before; only checked blocks count.
    checked = sources[: result.get("checked_blocks", 0)]
    scanned = {"native": checked.count("native"), "ocr": checked.count("ocr")}
    next_position = result.pop("next_position")
    return {
        "file_id": file.id,
        "version": file.sha256,
        "representation": {
            "kind": "available_text" if searching else "ocr" if reading_ocr else "native",
            "revision": index.id if index else str((file.meta or {}).get("ocr_cache_revision", 0)),
            "parser": index.parser_version if index else None,
            "ocr_provenance": "legacy_page_cache" if ocr_rows else "scoped_cache" if scoped_rows else None,
        },
        **result,
        **({"query": request.query, "match_mode": "normalized_literal"} if searching else {}),
        "next_cursor": encode_cursor(context, next_position, key=key) if next_position is not None else None,
        "truncated": next_position is not None,
        "navigation_status": "available_index" if navigation else "not_requested",
        "physical_page_count": ((file.meta or {}).get("extract") or {}).get("units")
        if ((file.meta or {}).get("extract") or {}).get("kind") == "pdf"
        else "unknown",
        "coverage": {
            "native": {
                "indexed_status": index.status if index else "unavailable",
                "unprepared_pages_or_regions": "unknown" if index and index.status == "partial" else "none_known",
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
