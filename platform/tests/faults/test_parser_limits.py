"""Real subprocess limit failures: watchdog termination and parser memory exhaustion."""

import io
import signal
import sys
import zipfile

import pytest
from docx import Document

from ailab_platform.worker import isolate
from ailab_platform.worker.isolate import IsolationError, execute


def test_parser_wall_timeout_kills_child(tmp_path, monkeypatch):
    # A tiny valid file may finish before the covered parent is scheduled again on Linux.
    # Keep a real child alive so this tests termination, independently of parser/startup speed.
    spawn = isolate.subprocess.Popen
    children = []

    def slow_parser(command, *args, **kwargs):
        if command[1:3] == ["-m", "ailab_platform.worker.isolate"]:
            command = [sys.executable, "-c", "import time; time.sleep(60)"]
            child = spawn(command, *args, **kwargs)
            children.append(child)
            return child
        return spawn(command, *args, **kwargs)

    monkeypatch.setattr(isolate.subprocess, "Popen", slow_parser)
    source = tmp_path / "file.txt"
    source.write_text("hello")
    with pytest.raises(IsolationError, match="timeout"):
        execute("extract", source, tmp_path / "output", timeout=0.05)
    assert len(children) == 1 and children[0].returncode == -signal.SIGKILL


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
