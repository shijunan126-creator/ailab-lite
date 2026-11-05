"""New Chat contracts, bounded scanning and cursor completeness independent of old runs."""

import math

import pytest
from pydantic import ValidationError

from ailab_platform.contracts.document_input import ReadDocument, SearchDocument, ViewDocument
from ailab_platform.domain.document_reading import (
    CursorError,
    Position,
    TextBlock,
    context_digest,
    decode_cursor,
    encode_cursor,
    read_batch,
    search_batch,
)


def test_search_does_not_drop_hits_at_twenty_result_boundary():
    blocks = [TextBlock(0, "page:1", "词 " * 53), TextBlock(1, "page:2", "词")]
    position, hits, counts = Position(), [], []
    while position is not None:
        result = search_batch((b for b in blocks if b.ordinal >= position.ordinal), "词", position)
        hits.extend((h["locator"], h["normalized_start"]) for h in result["hits"])
        counts.append(len(result["hits"]))
        position = result["next_position"]
    assert counts == [20, 20, 14]
    assert len(hits) == len(set(hits)) == 54
    assert hits[-1] == ("page:2", 0)


def test_zero_hit_batch_has_a_cursor_until_available_text_is_exhausted():
    blocks = [TextBlock(i, f"p:{i + 1}", "ordinary text") for i in range(250)]
    blocks[-1] = TextBlock(249, "p:250", "target")
    position, counts, hits = Position(), [], []
    while position is not None:
        result = search_batch((b for b in blocks if b.ordinal >= position.ordinal), "target", position)
        counts.append(result["checked_blocks"])
        hits.extend(result["hits"])
        position = result["next_position"]
    assert counts == [100, 100, 50]
    assert [h["locator"] for h in hits] == ["p:250"]


def test_search_literal_unicode_normalization_keeps_source_locator():
    result = search_batch([TextBlock(0, "page:12", "全角ＡＢＣ Straße Cafe\u0301 ﬃ .*", block_id="blk_1")], "café")
    hit = result["hits"][0]
    assert hit["locator"] == "page:12" and hit["block_id"] == "blk_1"
    assert hit["normalization"] == "NFKC_casefold"
    assert search_batch([TextBlock(0, "p:1", "Straße")], "STRASSE")["hits"]
    assert search_batch([TextBlock(0, "p:1", "全角ＡＢＣ")], "abc")["hits"]
    assert not search_batch([TextBlock(0, "p:1", "anything")], ".*")["hits"]


def test_cursor_binds_query_selector_and_every_representation_revision():
    context = dict(
        operation="search",
        file_id="fil_1",
        version="source",
        representation=["native_v4", "ocr_v1"],
        selector={"kind": "pages", "pages": [1]},
        query="target",
    )
    digest = context_digest(**context)
    token = encode_cursor(digest, Position(17, 320))
    assert decode_cursor(token, digest) == Position(17, 320)
    for key, value in [
        ("file_id", "fil_2"),
        ("version", "source2"),
        ("operation", "read"),
        ("query", "other"),
        ("selector", None),
        ("representation", ["native_v4", "ocr_v2"]),
    ]:
        with pytest.raises(CursorError, match="cursor_stale"):
            decode_cursor(token, context_digest(**{**context, key: value}))
    for bad in ["bad!", "", "a" * 2049, encode_cursor(digest, Position(-1)), encode_cursor(digest, Position(True))]:
        with pytest.raises(CursorError):
            decode_cursor(bad, digest)


def test_read_respects_structure_boundaries_and_continues_inside_oversized_block():
    blocks = [TextBlock(0, "p:1", "A" * 7000), TextBlock(1, "p:2", "B" * 1000), TextBlock(2, "p:3", "C" * 12000)]
    position, parts = Position(), []
    while position is not None:
        result = read_batch((b for b in blocks if b.ordinal >= position.ordinal), position)
        assert len(result["text"]) <= 8000
        parts.append(result["text"])
        position = result["next_position"]
    assert list(map(len, parts)) == [8000, 8000, 4000]
    assert "".join(parts) == "".join(b.text for b in blocks)
    short = read_batch([TextBlock(0, "p:1", "A" * 6000), TextBlock(1, "p:2", "B" * 3000)])
    assert len(short["text"]) == 6000 and short["next_position"] == Position(1)


def test_read_never_substitutes_ocr_and_preserves_empty_native_coverage():
    result = read_batch([TextBlock(0, "page:1", ""), TextBlock(1, "page:2", "页眉")])
    assert result["text"] == "页眉"
    assert [c["text_source"] for c in result["coverage"]] == ["native", "native"]
    assert len(result["coverage"]) == 2


def test_typed_selectors_are_mutually_exclusive_and_ocr_is_explicit():
    assert ReadDocument(file_id="fil_1").text_source == "native"
    assert ReadDocument(file_id="fil_1", text_source="ocr", selector={"kind": "pages", "pages": [4, 1]}).selector.pages == [1, 4]
    for payload in [
        {"query": "x"},
        {"text_source": "ocr"},
        {"selector": {"kind": "pages", "pages": [1], "anchor": "p:1"}},
        {"selector": {"kind": "pages", "pages": [True]}},
        {"selector": {"kind": "pages", "pages": [1, 1]}},
        {"selector": {"kind": "word", "anchor": "../../secret"}},
        {"selector": {"kind": "sheet", "sheet": "Revenue", "range": "XFE1"}},
        {"selector": {"kind": "sheet", "sheet": "Revenue", "range": "C5:B1"}},
    ]:
        with pytest.raises(ValidationError):
            ReadDocument(file_id="fil_1", **payload)
    with pytest.raises(ValidationError):
        SearchDocument(file_id="fil_1", query="   ")


def test_visual_regions_are_revision_rotation_and_pixel_space_bound():
    target = dict(kind="region", page=9, revision="render-v3", x=0.2, y=0.1, width=0.4, height=0.5, rotation=90)
    assert ViewDocument(file_id="fil_1", target=target).target.page == 9
    for patch in [{"x": math.nan}, {"height": 0}, {"rotation": 45}, {"revision": ""}, {"width": 0.9}]:
        with pytest.raises(ValidationError):
            ViewDocument(file_id="fil_1", target={**target, **patch})
    with pytest.raises(ValidationError):
        ViewDocument(file_id="fil_1", target={"kind": "pages", "pages": [1, 2, 3, 4, 5]})


def test_indexed_native_operations_page_without_ocr_or_eager_body_reads(monkeypatch):
    import hashlib

    from fastapi import HTTPException
    from sqlalchemy import select

    from ailab_platform.api.document_text import text_operation
    from ailab_platform.db import SessionLocal, init_db
    from ailab_platform.models import DocumentBlock, FileDerivation, FileObject, User, new_id
    from ailab_platform.storage import get_storage

    init_db()
    storage = get_storage()
    with SessionLocal() as db:
        user = db.scalar(select(User))
        file = FileObject(
            id=new_id("fil"), user_id=user.id, filename="document-operations.pdf", storage_key="original", sha256="source", status="ready"
        )
        db.add(file)
        db.flush()
        index = FileDerivation(
            file_id=file.id,
            kind="native",
            fingerprint="one",
            parser_version="office-a60ba6a-ailab4",
            status="ready",
            manifest_key=f"derived/{file.id}/office/generation/manifest.json",
        )
        db.add(index)
        db.flush()
        file.meta = {
            "extract": {"kind": "pdf", "units": 130},
            "representations": {"native_text": {"status": "ready", "version": "source", "derivation_id": index.id}},
        }
        key, offset, body = f"derived/{file.id}/office/generation/text.txt", 0, bytearray()
        for i in range(130):
            text = b"target " * 25 if i == 129 else b"available native text"
            db.add(
                DocumentBlock(
                    derivation_id=index.id,
                    ordinal=i,
                    unit_number=i + 1,
                    anchor=f"page:{i + 1}",
                    text_key=key,
                    digest=hashlib.sha256(text).hexdigest(),
                    chars=len(text),
                    byte_offset=offset,
                    byte_size=len(text),
                )
            )
            body.extend(text)
            offset += len(text)
        storage.put_bytes(key, bytes(body))
        db.commit()
        opens = []
        original = storage.open_stream

        def record(key, *args, **kwargs):
            opens.append(key)
            return original(key, *args, **kwargs)

        monkeypatch.setattr(storage, "open_stream", record)
        navigation = text_operation(db, file, ReadDocument(file_id=file.id, navigation_only=True))
        assert len(navigation["navigation"]) == 100 and navigation["next_cursor"]
        assert navigation["navigation"][0]["locator"] == "page:1" and navigation["physical_page_count"] == 130
        assert opens == []  # Navigation never opens canonical text or a PDF/render.
        continuation = text_operation(db, file, ReadDocument(file_id=file.id, navigation_only=True, cursor=navigation["next_cursor"]))
        assert len(continuation["navigation"]) == 30 and not continuation["next_cursor"] and opens == []
        with pytest.raises(HTTPException) as error:
            text_operation(db, file, ReadDocument(file_id=file.id, cursor=navigation["next_cursor"]))
        assert error.value.detail == "cursor_stale"
        first = text_operation(db, file, SearchDocument(file_id=file.id, query="target"))
        assert first["hits"] == [] and first["checked_blocks"] == 100 and first["next_cursor"]
        assert opens == [key]  # one canonical buffer, no per-block storage read
        second = text_operation(db, file, SearchDocument(file_id=file.id, query="target", cursor=first["next_cursor"]))
        assert len(second["hits"]) == 20 and second["next_cursor"]
        third = text_operation(db, file, SearchDocument(file_id=file.id, query="target", cursor=second["next_cursor"]))
        assert len(third["hits"]) == 5 and not third["next_cursor"]
        assert third["coverage"]["ocr"]["status"] == "not_searched"
        assert third["coverage"]["visual"]["status"] == "not_viewed"
        result = text_operation(db, file, ReadDocument(file_id=file.id, selector={"kind": "pages", "pages": [130]}))
        assert result["text"] == "target " * 25
        assert result["coverage"]["blocks"][0]["locator"] == "page:130"
        with pytest.raises(HTTPException) as error:
            text_operation(db, file, SearchDocument(file_id=file.id, query="changed", cursor=first["next_cursor"]))
        assert error.value.detail == "cursor_stale"
        with pytest.raises(HTTPException) as error:
            text_operation(db, file, ReadDocument(file_id=file.id, selector={"kind": "pages", "pages": [131]}))
        assert error.value.detail == "document_page_out_of_range"
        storage.delete(key)
