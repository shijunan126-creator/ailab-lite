"""Library sandbox rendering reuses public lease lifecycle, including error cleanup."""

import io
import shutil

import pytest
from docx import Document

from ailab_platform.broker.render import LibraryRenderer
from ailab_platform.broker.service import LeaseError
from tests.test_workspace_previews import _manager


def setup_renderer(tmp_path, content, filename):
    manager, api, storage = _manager(tmp_path)
    api.get_file = lambda fid: {
        "id": fid,
        "status": "ready",
        "filename": filename,
        "meta": {"preview_job_id": "job", "preview": {"status": "pending"}},
    }
    api.get_file_bytes = lambda fid: content
    return manager, LibraryRenderer(manager), storage


@pytest.mark.skipif(not all(shutil.which(x) for x in ["soffice", "pdftoppm", "pdfinfo", "timeout"]), reason="Office tools unavailable")
def test_docx_real_renderer_and_release(tmp_path):
    doc = Document()
    doc.add_paragraph("Library preview contract")
    data = io.BytesIO()
    doc.save(data)
    manager, renderer, storage = setup_renderer(tmp_path, data.getvalue(), "hello.docx")
    result = renderer.render("file", "job")
    assert result["preview"]["status"] == "ready" and result["preview"]["page_count"] == 1
    assert len(result["preview"]["pages"]) == 1
    key = next(iter(result["assets"].values()))["key"]
    assert storage.get_bytes(key).startswith(b"\xff\xd8")
    assert manager.active_leases() == [] and not renderer.contexts.contexts


def test_failed_renderer_releases_and_job_id_is_bound(tmp_path):
    manager, renderer, _ = setup_renderer(tmp_path, b"broken", "bad.pdf")
    with pytest.raises(LeaseError, match="pending"):
        renderer.render("file", "wrong")
    with pytest.raises(LeaseError, match="render_failed"):
        renderer.render("file", "job")
    assert manager.active_leases() == [] and not renderer.contexts.contexts


@pytest.mark.skipif(not all(shutil.which(x) for x in ["pdftoppm", "pdfinfo", "timeout"]), reason="PDF tools unavailable")
def test_pdf_caps_at_40_but_reports_total_page_count(tmp_path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(57):
        writer.add_blank_page(width=72, height=72)
    data = io.BytesIO()
    writer.write(data)
    manager, renderer, _ = setup_renderer(tmp_path, data.getvalue(), "57pages.pdf")
    result = renderer.render("file", "job")
    assert result["preview"]["page_count"] == 57 and len(result["preview"]["pages"]) == 40
    assert manager.active_leases() == []
