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
    api.get_file = lambda fid, **bound: {
        "id": fid,
        "status": "ready",
        "filename": filename,
        "meta": {"preview_job_id": "job", "preview": {"status": "pending"}},
    }
    api.get_file_bytes = lambda fid, **bound: content
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
    assert manager.active_leases() == []


def test_failed_renderer_releases_and_job_id_is_bound(tmp_path):
    manager, renderer, _ = setup_renderer(tmp_path, b"broken", "bad.pdf")
    with pytest.raises(LeaseError, match="pending"):
        renderer.render("file", "wrong")
    with pytest.raises(LeaseError, match="render_failed"):
        renderer.render("file", "job")
    assert manager.active_leases() == []


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


def test_render_uses_the_reserved_render_class_and_reads_bound_to_its_job(tmp_path):
    """A render is a system lease of the render profile; every file read names the preview job."""
    manager, renderer, _ = setup_renderer(tmp_path, b"broken", "bad.pdf")
    reads, acquired = [], []
    original_get = manager.platform.get_file_bytes
    manager.platform.get_file_bytes = lambda fid, **bound: reads.append(bound) or original_get(fid, **bound)
    original_acquire = manager.acquire
    manager.acquire = lambda spec, principal, **kw: acquired.append((spec, principal)) or original_acquire(spec, principal, **kw)
    with pytest.raises(LeaseError, match="render_failed"):
        renderer.render("file", "job")
    spec, principal = acquired[0]
    assert spec.profile == "render" and principal.system and spec.max_lifetime <= 120
    assert reads and all(r.get("job") == "preview" and r.get("preview_job") == "job" for r in reads)
    assert manager.active_leases() == []
