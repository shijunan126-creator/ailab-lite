import fcntl
import os
from types import SimpleNamespace

import pytest

from ailab_platform.sandbox_tools import work_view_listener as listener


def test_existing_listener_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(listener, "ROOT", tmp_path)
    monkeypatch.setattr(listener.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must reuse listener"))
    with (tmp_path / "lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        listener.ensure(100)
    assert float((tmp_path / "activity").read_text()) > 0


@pytest.mark.parametrize("reason", ["idle", "rss"])
def test_listener_exits_when_idle_or_over_rss_limit(tmp_path, monkeypatch, reason):
    monkeypatch.setattr(listener, "ROOT", tmp_path)
    monkeypatch.setattr(listener.time, "monotonic", lambda: 200.0)
    (tmp_path / "activity").write_text("0" if reason == "idle" else "200")
    actions = []
    commands = []
    process = SimpleNamespace(pid=123, poll=lambda: None, terminate=lambda: actions.append("terminate"), wait=lambda timeout=0: 0)
    monkeypatch.setattr(listener.subprocess, "Popen", lambda command, **kwargs: commands.append(command) or process)
    monkeypatch.setattr(listener, "memory_bytes", lambda pid: 200 if reason == "rss" else 1)
    listener.serve(100)
    assert actions == ["terminate"]
    assert commands[0][0].endswith("/soffice.bin")


def test_pdf_cache_reuses_content_and_invalidates_edits_suffix_and_renderer(tmp_path, monkeypatch):
    monkeypatch.setattr(listener, "ROOT", tmp_path)
    source = tmp_path / "input.pptx"
    source.write_bytes(b"version one")
    calls = []

    def convert(path, fmt, output):
        calls.append(path.read_bytes())
        pdf = output / "input.pdf"
        pdf.write_bytes(b"PDF " + path.read_bytes())
        return pdf

    for revision, expected_hit in [(b"renderer1", False), (b"renderer1", True), (b"renderer2", False)]:
        with listener.pdf_source(source, convert, revision) as (pdf, hit):
            assert hit is expected_hit and pdf.read_bytes() == b"PDF version one"
    source.write_bytes(b"version two")
    with listener.pdf_source(source, convert, b"renderer2") as (pdf, hit):
        assert not hit and pdf.read_bytes() == b"PDF version two"
    source = source.rename(tmp_path / "input.docx")
    with listener.pdf_source(source, convert, b"renderer2") as (_, hit):
        assert not hit
    assert len(calls) == 4


@pytest.mark.parametrize("code,launches", [(81, 2), (1, 1)])
def test_listener_handles_profile_initialization_exit_only_once(tmp_path, monkeypatch, code, launches):
    monkeypatch.setattr(listener, "ROOT", tmp_path)
    (tmp_path / "activity").write_text("0")
    monkeypatch.setattr(listener.time, "monotonic", lambda: 200)
    monkeypatch.setattr(listener, "memory_bytes", lambda pid: 1)
    processes = []

    def launch(*args, **kwargs):
        process = SimpleNamespace(pid=123, poll=lambda: code, terminate=lambda: None, wait=lambda timeout=0: code)
        processes.append(process)
        return process

    monkeypatch.setattr(listener.subprocess, "Popen", launch)
    listener.serve(100)
    assert len(processes) == launches


def test_pdf_cache_bounds_expiry_abandoned_exports_and_active_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(listener, "ROOT", tmp_path)
    monkeypatch.setattr(listener, "CACHE_LIMIT", 10)
    monkeypatch.setattr(listener, "CACHE_ENTRIES", 2)
    source = tmp_path / "input.docx"

    def convert(path, fmt, output):
        pdf = output / "input.pdf"
        pdf.write_bytes(path.read_bytes())
        return pdf

    abandoned = tmp_path / "pdf-export-abandoned"
    abandoned.mkdir()
    (abandoned / "partial.pdf").write_bytes(b"partial")
    for content in (b"first", b"second", b"third"):
        source.write_bytes(content)
        with listener.pdf_source(source, convert, b"v1") as (pdf, _):
            listener.expire_cache(all_entries=True)
            assert pdf.exists()
        assert sum(path.stat().st_size for path in (tmp_path / "pdf-cache").iterdir()) <= 10
    assert not abandoned.exists()
    assert len(list((tmp_path / "pdf-cache").iterdir())) <= 2
    for path in (tmp_path / "pdf-cache").iterdir():
        os.utime(path, (0, 0))
    listener.expire_cache()
    assert list((tmp_path / "pdf-cache").iterdir()) == []


def test_pdf_cache_cleans_failed_exports_and_does_not_cache_oversized_pdfs(tmp_path, monkeypatch):
    monkeypatch.setattr(listener, "ROOT", tmp_path)
    monkeypatch.setattr(listener, "PDF_LIMIT", 3)
    source = tmp_path / "input.docx"
    source.write_bytes(b"source")

    def convert(path, fmt, output):
        pdf = output / "input.pdf"
        pdf.write_bytes(b"oversized")
        return pdf

    with listener.pdf_source(source, convert, b"v1") as (pdf, hit):
        assert not hit and pdf.read_bytes() == b"oversized"
    assert not pdf.exists() and list((tmp_path / "pdf-cache").iterdir()) == []

    def failed(path, fmt, output):
        (output / "partial.pdf").write_bytes(b"partial")
        raise RuntimeError("failed export")

    with pytest.raises(RuntimeError, match="failed export"), listener.pdf_source(source, failed, b"v1"):
        pytest.fail("failed export must not yield")
    assert list(tmp_path.glob("pdf-export-*")) == []
    monkeypatch.setattr(listener, "PDF_LIMIT", 100)
    with pytest.raises(RuntimeError, match="invalid PDF"), listener.pdf_source(source, convert, b"v1"):
        raise RuntimeError("invalid PDF")
    assert list((tmp_path / "pdf-cache").iterdir()) == []


def test_pdf_cache_never_follows_cached_symlinks(tmp_path, monkeypatch):
    monkeypatch.setattr(listener, "ROOT", tmp_path)
    cache = tmp_path / "pdf-cache"
    cache.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    (cache / "unsafe.pdf").symlink_to(outside)
    listener.expire_cache()
    assert not (cache / "unsafe.pdf").exists() and outside.read_bytes() == b"keep"
