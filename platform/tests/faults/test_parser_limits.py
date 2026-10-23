"""Real parser processes exceed limits; no mocked success path can satisfy these."""

import io
import zipfile

import pytest
from docx import Document

from ailab_platform.worker.isolate import IsolationError, execute


def test_parser_wall_timeout_kills_child(tmp_path):
    source = tmp_path / "file.txt"
    source.write_text("hello")
    with pytest.raises(IsolationError, match="timeout"):
        execute("extract", source, tmp_path / "output", timeout=0.001)


def test_parser_memory_limit_kills_or_reports_oom(tmp_path):
    doc = Document()
    doc.add_paragraph("hello")
    data = io.BytesIO()
    doc.save(data)
    source = tmp_path / "large.docx"
    with zipfile.ZipFile(io.BytesIO(data.getvalue())) as original, zipfile.ZipFile(source, "w", zipfile.ZIP_STORED) as target:
        for item in original.infolist():
            content = original.read(item.filename)
            if item.filename == "word/document.xml":
                content = (
                    b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                    + b"<w:p><w:r><w:t>x</w:t></w:r></w:p>" * 600_000
                    + b"</w:body></w:document>"
                )
            target.writestr(item.filename, content)
    with pytest.raises(IsolationError, match=r"memory_limit|resource_limit"):
        execute("extract", source, tmp_path / "output", memory_mb=96, timeout=10)
