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


def test_shards_split_the_browser_suite_into_disjoint_parts(monkeypatch):
    class Item:
        def __init__(self, nodeid):
            self.nodeid = nodeid

    class Config:
        class hook:
            @staticmethod
            def pytest_deselected(items):
                pass

    ids = [f"e2e/test_x.py::test_{n}" for n in range(60)]
    monkeypatch.setenv("AILAB_E2E", "1")
    seen = []
    for index in (1, 2, 3):
        monkeypatch.setenv("AILAB_E2E_SHARD", f"{index}/3")
        items = [Item(nodeid) for nodeid in ids]
        harness.pytest_collection_modifyitems(Config, items)
        assert items  # each shard gets work
        seen += [item.nodeid for item in items]
    assert sorted(seen) == sorted(ids)
    monkeypatch.setenv("AILAB_E2E_SHARD", "4/3")
    with pytest.raises(pytest.UsageError):
        harness.pytest_collection_modifyitems(Config, [Item(ids[0])])
