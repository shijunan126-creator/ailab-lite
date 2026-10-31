from types import SimpleNamespace

import pytest

from ailab_platform.sandbox_tools import render_preview


@pytest.mark.parametrize("listener_fails", [False, True])
def test_listener_conversion_and_cold_fallback(tmp_path, monkeypatch, listener_fails):
    source = tmp_path / "document.docx"
    source.write_bytes(b"input")
    calls = []
    monkeypatch.setenv("AILAB_LO_LISTENER", "1")
    monkeypatch.setenv("AILAB_LO_PROFILE", "/tmp/listener-profile")

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "/usr/bin/python3" and listener_fails:
            raise render_preview.subprocess.CalledProcessError(1, command)
        (tmp_path / "document.pdf").write_bytes(b"pdf")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(render_preview.subprocess, "run", run)
    assert render_preview.soffice(source, "pdf", tmp_path).read_bytes() == b"pdf"
    assert calls[0][1] == "/workspace/.ailab/uno_convert.py"
    assert len(calls) == (2 if listener_fails else 1)
    if listener_fails:
        assert calls[1][0] == "soffice"
        assert "/tmp/listener-profile" not in " ".join(calls[1])


def test_strict_render_does_not_hide_a_failed_listener(tmp_path, monkeypatch):
    source = tmp_path / "document.docx"
    source.write_bytes(b"input")
    monkeypatch.setenv("AILAB_LO_LISTENER", "1")
    monkeypatch.setenv("AILAB_LO_STRICT", "1")
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        raise render_preview.subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(render_preview.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="listener failed"):
        render_preview.soffice(source, "pdf", tmp_path)
    assert len(calls) == 1
