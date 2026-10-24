"""Harness failures must preserve diagnostics and reject an unrelated HTTP listener."""

import subprocess
import sys

import httpx
import pytest

from e2e import conftest as harness


def test_build_command_preserves_output_on_failure(tmp_path):
    log = tmp_path / "build.log"
    with pytest.raises(subprocess.CalledProcessError):
        harness._run_logged([sys.executable, "-c", "print('compiler detail', flush=True); raise SystemExit(2)"], tmp_path, log, 5)
    assert "compiler detail" in log.read_text()


def test_readiness_rejects_wrong_listener(monkeypatch):
    monkeypatch.setattr(harness.httpx, "get", lambda *a, **kw: httpx.Response(404))
    ticks = iter([0, 0, 2])
    monkeypatch.setattr(harness.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(harness.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="did not come up"):
        harness._wait("http://127.0.0.1:1/login", "portal", seconds=1)
