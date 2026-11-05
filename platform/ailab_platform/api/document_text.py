"""Authorized-source text operations; transport and run/catalog admission are separate."""

from fastapi import HTTPException
from sqlalchemy import select

from ..contracts.document_input import Pages, ReadDocument, SearchDocument, Sheet, Word
from ..domain.document_reading import (
    CursorError,
    TextBlock,
    context_digest,
    decode_cursor,
    encode_cursor,
    read_batch,
    search_batch,
)
from ..models import DocumentBlock, FileDerivation
from ..native_text import NativeCacheError, read_unit
from ..storage import get_storage
from .native_preparation import native_state


def current_index(db, file):
    state = native_state(file)
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


def text_operation(db, file, request: ReadDocument | SearchDocument):
    """Consumes an already authorized immutable file; never starts parsing or OCR.

    This service is deliberately not mounted as a legacy route. New-run catalog,
    pending-job orchestration and OCR coverage admission must wrap it at rollout.
    """
    searching = isinstance(request, SearchDocument)
    if not searching and request.text_source != "native":
        raise HTTPException(409, "ocr_representation_unavailable")
    index = current_index(db, file)
    selector = request.selector.model_dump(mode="json") if request.selector else None
    context = context_digest(
        operation="search" if searching else "read",
        file_id=file.id,
        version=file.sha256,
        representation=index.id,
        selector=selector,
        query=request.query if searching else None,
    )
    try:
        position = decode_cursor(request.cursor, context)
        rows = db.scalars(select_blocks(file, index, request.selector, position)).all()
        storage, cache = get_storage(), {}

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
                yield TextBlock(row.ordinal, row.anchor, text, block_id=row.id)

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
        "representation": {"kind": "native", "revision": index.id, "parser": index.parser_version},
        **result,
        "next_cursor": encode_cursor(context, next_position) if next_position is not None else None,
        "truncated": next_position is not None,
        "coverage": {
            "native": {"indexed_status": index.status, "end_of_available_text": next_position is None},
            "visual": {"status": "not_viewed"},
            "ocr": {"status": "not_searched" if searching else "not_read"},
            "scope": selector or {"kind": "available_native_text"},
            "blocks": result.get("coverage", []),
        },
    }
