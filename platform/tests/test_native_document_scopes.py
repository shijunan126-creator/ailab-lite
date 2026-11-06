"""Physical PDF ranges and Office projections keep coverage and resource facts separate."""

import hashlib
import io
import json
import subprocess

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from ailab_platform.api.document_content import visual_info, visual_pages
from ailab_platform.api.document_sources import index_document
from ailab_platform.api.document_text import text_operation
from ailab_platform.api.native_ranges import cached_scope, charge_consumption, missing_scope
from ailab_platform.contracts.document_input import Pages, ReadDocument, SearchDocument
from ailab_platform.db import SessionLocal, init_db
from ailab_platform.models import DocumentNativeRange, FileObject, Run, User, new_id
from ailab_platform.native_text import PARSER_NATIVE
from ailab_platform.storage import get_storage
from tests.test_native_text_ranges import pdf_bytes, runner


def scoped_parse(tmp_path, data, kind, selector):
    source, output, request = tmp_path / ("source." + kind), tmp_path / "result", tmp_path / "request.json"
    source.write_bytes(data)
    request.write_text(json.dumps(selector))
    result = subprocess.run(["node", str(runner()), str(source), str(output), kind, "scope", str(request)], capture_output=True, timeout=60)
    return result, output


def test_pdf_extracts_only_explicit_pages_and_retains_actual_total(tmp_path):
    result, output = scoped_parse(tmp_path, pdf_bytes(500), "pdf", {"kind": "pages", "pages": [2, 499]})
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["total_units"] == 500 and manifest["status"] == "partial"
    assert manifest["coverage"] == ["page:2", "page:499"]
    assert manifest["visual_status"] == "unknown"
    text = (output / "text.txt").read_text()
    assert "Page 2:" in text and "Page 499:" in text
    assert "Page 1:" not in text and "Page 500:" not in text
    assert sorted(p.name for p in output.iterdir()) == ["manifest.json", "text.txt"]


@pytest.mark.parametrize("selector", [{"kind": "pages", "pages": [4]}, {"kind": "pages", "pages": [1, 1]}])
def test_invalid_pdf_scope_never_publishes_a_manifest(tmp_path, selector):
    result, output = scoped_parse(tmp_path, pdf_bytes(3), "pdf", selector)
    assert result.returncode != 0
    assert not (output / "manifest.json").exists()


def test_word_cell_projection_keeps_source_cost_and_does_not_return_other_cells(tmp_path):
    from docx import Document

    document = Document()
    document.add_paragraph("A different paragraph")
    table = document.add_table(rows=2, cols=2)
    for row, cells in enumerate(table.rows):
        for col, cell in enumerate(cells.cells):
            cell.text = f"Cell {row + 1},{col + 1}"
    data = io.BytesIO()
    document.save(data)
    result, output = scoped_parse(tmp_path, data.getvalue(), "docx", {"kind": "word", "anchor": "t:1/r2c2"})
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    text = (output / "text.txt").read_text()
    assert "Cell 2,2" in text and "Cell 1,1" not in text and "different paragraph" not in text
    assert manifest["source_chars"] > manifest["chars"]
    assert "whole package" in " ".join(manifest["limitations"])


def test_tiny_office_projection_cannot_hide_source_text_limit(tmp_path):
    from docx import Document

    document = Document()
    document.add_paragraph("target")
    for _ in range(210):
        document.add_paragraph("text " * 220)
    data = io.BytesIO()
    document.save(data)
    result, output = scoped_parse(tmp_path, data.getvalue(), "docx", {"kind": "word", "anchor": "p:1"})
    assert result.returncode != 0 and b"document_text_limit" in result.stderr
    assert not (output / "manifest.json").exists()


def test_probe_unknown_never_means_no_visuals_or_allocates_all_page_numbers():
    file = type("File", (), {"content_type": "application/pdf", "filename": "long.pdf"})()
    manifest = {"kind": "pdf", "total_units": 2147483647, "visual_units": [], "visual_status": "unknown"}
    assert visual_info(file, manifest)["status"] == "unknown"
    assert visual_pages(file, manifest) is None


def make_file(db, *, total=500):
    user = db.scalar(select(User))
    file = FileObject(
        id=new_id("fil"),
        user_id=user.id,
        filename=new_id("scope") + ".pdf",
        storage_key="test-original",
        sha256="range-source",
        status="ready",
        size=1024,
        content_type="application/pdf",
        meta={"scan": {"verdict": "clean"}},
    )
    db.add(file)
    db.flush()
    prefix = f"derived/{file.id}/office/base/"
    manifest = {"parser": PARSER_NATIVE, "version": 2, "kind": "pdf", "units": [], "status": "partial", "chars": 0, "total_units": total}
    index = index_document(db, file, prefix + "manifest.json", manifest=manifest)
    file.meta = {
        **file.meta,
        "extract": {"kind": "pdf", "units": total, "chars": 0, "source_blocks": 0},
        "representations": {"native_text": {"status": "ready", "version": file.sha256, "derivation_id": index.id}},
    }
    db.flush()
    return file, index


def test_billion_page_navigation_is_bounded_and_does_not_read_bodies(monkeypatch):
    init_db()
    with SessionLocal() as db:
        file, _ = make_file(db, total=2147483647)
        monkeypatch.setattr("ailab_platform.api.document_text.get_storage", lambda: pytest.fail("navigation must not read bodies"))
        request = ReadDocument(file_id=file.id, navigation_only=True)
        result = text_operation(db, file, request)
        assert len(result["navigation"]) == 100 and result["truncated"]
        assert result["navigation"][0]["locator"] == "page:1"
        second = text_operation(db, file, request.model_copy(update={"cursor": result["next_cursor"]}))
        assert second["navigation"][0]["locator"] == "page:101"
        assert second["physical_page_count"] == 2147483647


def test_scoped_pages_join_available_search_and_base_generation_fences_reuse():
    from ailab_platform.api.document_ocr import scope_key

    init_db()
    with SessionLocal() as db:
        file, index = make_file(db)
        selector = Pages(kind="pages", pages=[499])
        assert missing_scope(db, file, index, selector) == {"kind": "pages", "pages": [499]}
        text = b"Selected native page with needle"
        key = f"derived/{file.id}/office/scope/manifest.json"
        storage = get_storage()
        storage.put_bytes(key.rsplit("/", 1)[0] + "/text.txt", text)
        unit = {
            "ordinal": 0,
            "anchor": "page:499",
            "unit_number": 499,
            "name": "text.txt",
            "chars": len(text),
            "sha256": hashlib.sha256(text).hexdigest(),
            "byte_offset": 0,
            "byte_size": len(text),
        }
        derived = index_document(
            db,
            file,
            key,
            kind="native-scope",
            manifest={"parser": PARSER_NATIVE, "version": 2, "kind": "pdf", "units": [unit], "status": "partial"},
        )
        scope = {"kind": "pages", "pages": [499]}
        db.add(
            DocumentNativeRange(
                file_id=file.id,
                source_version=file.sha256,
                parser=PARSER_NATIVE,
                base_derivation_id=index.id,
                selector_key=scope_key(scope),
                selector=scope,
                ordinal=0,
                page=499,
                derivation_id=derived.id,
            )
        )
        db.flush()
        assert missing_scope(db, file, index, selector) is None
        read = text_operation(db, file, ReadDocument(file_id=file.id, selector=selector))
        assert read["text"] == text.decode() and read["coverage"]["blocks"][0]["locator"] == "page:499"
        search = text_operation(db, file, SearchDocument(file_id=file.id, query="needle"))
        assert len(search["hits"]) == 1 and search["hits"][0]["locator"] == "page:499"
        file.meta = {**file.meta, "representations": {"native_text": {"derivation_id": "new-generation"}}}
        assert cached_scope(db, file, scope) is None


def test_consuming_small_scope_still_charges_entire_source_and_round_budget():
    init_db()
    with SessionLocal() as db:
        file, _ = make_file(db)
        run = Run(id=new_id("run"), user_id=file.user_id, model="mock", prompt="scope", runtime_meta={})
        db.add(run)
        db.flush()
        file.size = 20 * 1024**2
        charge_consumption(db, run, file)
        assert run.runtime_meta["document_inputs"][file.id]["bytes"] == file.size
        charge_consumption(db, run, file)  # cached re-read does not double-charge
        other, _ = make_file(db)
        other.size = file.size
        charge_consumption(db, run, other)
        third, _ = make_file(db)
        with pytest.raises(HTTPException, match="document_input_limit"):
            charge_consumption(db, run, third)
