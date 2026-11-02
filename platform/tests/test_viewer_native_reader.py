"""Exercise the real pinned runtime's check() in the limited reader process."""

import pytest

from ailab_platform.config import settings
from ailab_platform.worker.isolate import IsolationError, execute


def test_native_word_cells_ranges_and_invalid_locator(tmp_path):
    from docx import Document

    if not settings.viewer_reader_path.exists():
        pytest.skip("build the viewer reader or set AILAB_VIEWER_READER_PATH")
    source = tmp_path / "report.docx"
    doc = Document()
    doc.add_paragraph("first paragraph")
    doc.add_paragraph("second paragraph")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "left cell"
    table.cell(0, 1).text = "right cell"
    doc.save(source)
    result = execute(
        "viewer", source, tmp_path / "out", timeout=22, reader=str(settings.viewer_reader_path), selections=["p:1-2", "t:1/r1c2"]
    )
    assert "first paragraph" in result["selections"][0]["text"]
    assert "second paragraph" in result["selections"][0]["text"]
    assert "right cell" in result["selections"][1]["text"]
    with pytest.raises(IsolationError):
        execute("viewer", source, tmp_path / "bad", timeout=22, reader=str(settings.viewer_reader_path), selections=["p:99"])


def test_workspace_snapshot_refuses_symlinks_and_tracks_content(tmp_path):
    from ailab_platform.sandbox_tools.workspace_document import snapshot

    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "report.pdf"
    source.write_bytes(b"first")
    first = snapshot(root, source, tmp_path / "one")
    source.write_bytes(b"second")
    second = snapshot(root, source, tmp_path / "two")
    assert first["version"] != second["version"]
    (root / "alias.pdf").symlink_to(source)
    with pytest.raises(OSError):
        snapshot(root, root / "alias.pdf", tmp_path / "link")
    with pytest.raises(ValueError):
        snapshot(root, tmp_path / "outside", tmp_path / "outside-copy")


def test_live_workspace_uses_shared_reader_and_rejects_other_owner(tmp_path, monkeypatch):
    import re

    from docx import Document
    from fastapi import HTTPException

    from ailab_platform.broker.viewer import workspace
    from ailab_platform.storage import LocalStorage
    from tests.test_broker_lease import _manager

    if not settings.viewer_reader_path.exists():
        pytest.skip("build the viewer reader or set AILAB_VIEWER_READER_PATH")
    manager, provider, _ = _manager()
    manager.storage = LocalStorage(tmp_path / "objects")
    original = provider._translate_command

    def translate(sandbox_id, command):
        return re.sub(
            r"/[^ ]*/vendor/[^ ]+/node_modules", str(settings.viewer_reader_path.parent / "node_modules"), original(sandbox_id, command)
        )

    monkeypatch.setattr(provider, "_translate_command", translate)
    lease = manager.create("run_viewer")
    source = tmp_path / "draft.docx"
    doc = Document()
    doc.add_paragraph("workspace quote")
    doc.save(source)
    manager.put_file(lease["lease_id"], "/workspace/outputs/draft.docx", source.read_bytes())
    try:
        index = workspace(manager, "run_viewer", "user_test", "outputs/draft.docx", "document")
        assert index["status"] == "ready", index
        assert index["blocks"][0]["at"] == "p:1"
        selected = workspace(manager, "run_viewer", "user_test", "outputs/draft.docx", "check", ["p:1"], index["version"])
        assert "workspace quote" in selected["selections"][0]["text"]
        with pytest.raises(HTTPException) as error:
            workspace(manager, "run_viewer", "another-user", "outputs/draft.docx", "document")
        assert error.value.status_code == 410
        with pytest.raises(HTTPException) as error:
            workspace(manager, "run_viewer", "user_test", "outputs/draft.docx", "check", ["p:1"], "old-version")
        assert error.value.status_code == 409
    finally:
        manager.release(lease["lease_id"])
        manager.close()


@pytest.mark.parametrize(
    "kind,at,expected", [("pdf", "page:1", ""), ("xlsx", "Data!A1:B1", "cell quote"), ("pptx", "slide:1/shape:2", "slide quote")]
)
def test_native_other_formats_validate_exact_locations(tmp_path, kind, at, expected):
    if not settings.viewer_reader_path.exists():
        pytest.skip("build the viewer reader or set AILAB_VIEWER_READER_PATH")
    source = tmp_path / f"document.{kind}"
    if kind == "pdf":
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with source.open("wb") as stream:
            writer.write(stream)
    elif kind == "xlsx":
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.title = "Data"
        workbook.active.append(["cell quote", "value"])
        workbook.save(source)
    else:
        from pptx import Presentation

        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[0])
        slide.shapes.title.text = "slide quote"
        presentation.save(source)
    parsed = execute("viewer", source, tmp_path / "read", timeout=22, reader=str(settings.viewer_reader_path), selections=[at])
    assert parsed["kind"] == kind
    assert parsed["selections"][0]["at"] == at
    assert expected in parsed["selections"][0]["text"]
    if kind == "xlsx":
        assert parsed["cells"] == 2


def test_viewer_redelivery_reuses_cached_content_revision(tmp_path, monkeypatch):
    import json

    from ailab_platform.contracts.document_view import PARSER_REVISION
    from ailab_platform.storage import LocalStorage
    from ailab_platform.worker import viewer

    storage = LocalStorage(tmp_path / "storage")
    expected = {"status": "ready", "version": "same-hash", "parser_revision": PARSER_REVISION, "native": True}
    storage.put_bytes("derived/file/viewer.json", json.dumps(expected).encode())
    monkeypatch.setattr("ailab_platform.storage.get_storage", lambda: storage)

    class Platform:
        def get_file(self, file_id, **kwargs):
            assert file_id == "file" and kwargs == {"job": "viewer"}
            return {"id": "file", "sha256": "same-hash"}

    assert viewer.process_viewer(Platform(), "file") == expected


def test_snapshot_response_cleans_spool_when_send_fails(tmp_path):
    import asyncio

    from ailab_platform.broker.viewer import SnapshotResponse

    local = tmp_path / "spool"
    local.write_bytes(b"content")
    response = SnapshotResponse(local)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_):
        raise RuntimeError("client disconnected")

    with pytest.raises(RuntimeError, match="client disconnected"):
        asyncio.run(
            response({"type": "http", "method": "GET", "headers": [], "extensions": {}, "asgi": {"spec_version": "2.4"}}, receive, send)
        )
    assert not local.exists()


def test_workspace_upstream_is_closed_before_first_byte_on_disconnect():
    from ailab_platform.api.workspace_documents import WorkspaceStream

    class Response:
        closed = False

        def iter_bytes(self, size):
            yield b"bytes"

        def close(self):
            self.closed = True

    class Client:
        closed = False

        def close(self):
            self.closed = True

    response, client = Response(), Client()
    stream = WorkspaceStream(response, client)
    stream.close()
    assert response.closed and client.closed


@pytest.mark.parametrize("kind", ["docx", "xlsx", "pptx", "pdf"])
def test_oversize_native_view_falls_back_without_download_or_parse(tmp_path, monkeypatch, kind):
    from ailab_platform.contracts.document_view import NATIVE_BYTES
    from ailab_platform.storage import LocalStorage
    from ailab_platform.worker.viewer import process_viewer

    storage = LocalStorage(tmp_path / "storage")
    monkeypatch.setattr("ailab_platform.storage.get_storage", lambda: storage)

    class Platform:
        def get_file(self, file_id, **kwargs):
            return {"id": file_id, "sha256": "hash", "filename": "large." + kind, "size": NATIVE_BYTES[kind] + 1}

    result = process_viewer(Platform(), "large")
    assert result["status"] == "ready" and result["native"] is False
    assert result["reason"] == "native_admission_limit"
    assert storage.exists("derived/large/viewer.json")


def test_spreadsheet_selection_text_matches_viewer_tsv_without_table_labels(tmp_path):
    from openpyxl import Workbook

    from ailab_platform.api.document_view import normalized

    if not settings.viewer_reader_path.exists():
        pytest.skip("build the viewer reader or set AILAB_VIEWER_READER_PATH")
    source = tmp_path / "quote.xlsx"
    workbook = Workbook()
    workbook.active.title = "Revenue"
    workbook.active.append(["North | South", 100, 110])
    workbook.active.append(["East", 80, 90])
    workbook.save(source)
    parsed = execute(
        "viewer", source, tmp_path / "selection", timeout=22, reader=str(settings.viewer_reader_path), selections=["Revenue!A1:C2"]
    )
    selected = normalized(parsed["selections"][0]["text"])
    assert selected == normalized("North | South\t100\t110\nEast\t80\t90")
    assert normalized("North | South\t999\t110") not in selected
    assert normalized("1\tNorth | South") not in selected
