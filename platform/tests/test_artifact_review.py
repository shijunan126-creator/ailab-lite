"""Review evidence is bounded, read-only, and never an automatic quality pass."""

import hashlib
import importlib.util
import shutil
from pathlib import Path

import pytest
from docx import Document
from openpyxl import Workbook

from ailab_platform.broker.workspace_view import view
from tests.test_workspace_previews import _manager

HELPER = Path(__file__).resolve().parents[1] / "ailab_platform/sandbox_tools/artifact_checks.py"
spec = importlib.util.spec_from_file_location("artifact_checks", HELPER)
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


def test_structural_checks_detect_formula_errors_without_mutating_source(tmp_path):
    source = tmp_path / "formula.xlsx"
    book = Workbook()
    book.active["A1"] = "=1+1"
    book.active["A2"] = "#DIV/0!"
    book.save(source)
    before = source.read_bytes()
    result = checks.check(source)
    assert result["formulas"] == 1 and result["formula_errors"] == 1
    assert result["issues"] == ["cached_formula_errors"]
    assert result["sha256"] == hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == before


def test_docx_checks_and_invalid_package(tmp_path):
    source = tmp_path / "report.docx"
    doc = Document()
    doc.add_paragraph("Actual content")
    doc.save(source)
    assert checks.check(source)["paragraphs"] == 1
    import zipfile

    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("unrelated.xml", "<document/>")
    with pytest.raises(ValueError, match="invalid_office_structure"):
        checks.check(source)


@pytest.mark.skipif(not all(shutil.which(x) for x in ["pdfinfo", "pdftoppm", "timeout"]), reason="PDF tools unavailable")
def test_review_returns_page_coverage_and_pending_judgment(tmp_path):
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    data = io.BytesIO()
    writer.write(data)
    manager, _, _ = _manager(tmp_path)
    try:
        lease = manager.create("review")["lease_id"]
        manager.put_file(lease, "/workspace/outputs/result.pdf", data.getvalue())
        result = view(manager, lease, "/workspace/outputs/result.pdf", [2], review=True)
        assert result["structural"]["pages"] == 2
        assert result["coverage"] == {"reviewed_pages": [2], "complete": False}
        assert result["quality_gate"] == "pending_model_judgment"
        assert len(result["images"]) == 1
        assert manager.get_file(lease, "/workspace/outputs/result.pdf") == data.getvalue()
        for path in ["/workspace/inputs/a.pdf", "/workspace/outputs/a.png", "/workspace/../etc/a.pdf"]:
            with pytest.raises(ValueError):
                view(manager, lease, path, [1], review=True)
        with pytest.raises(ValueError, match="invalid_page_selection"):
            view(manager, lease, "/workspace/outputs/result.pdf", [1] * 5, review=True)
    finally:
        manager.close()
