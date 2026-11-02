"""A broken native index must report its cause before any long viewer wait."""

from types import SimpleNamespace

import pytest

from e2e.conftest import _viewer_reader
from e2e.test_office_native import _index


def test_explicit_viewer_reader_must_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("AILAB_VIEWER_READER_PATH", str(tmp_path / "missing.mjs"))
    with pytest.raises(RuntimeError, match="viewer reader is missing"):
        _viewer_reader(tmp_path, "mock")


@pytest.mark.parametrize("status", ["failed", "unsupported"])
def test_native_index_failure_does_not_wait(status):
    def unexpected_wait(_):
        pytest.fail("terminal native index must not wait")

    page = SimpleNamespace(
        request=SimpleNamespace(
            get=lambda _: SimpleNamespace(ok=True, json=lambda: {"status": status, "reason": "viewer_reader_unavailable"})
        ),
        wait_for_timeout=unexpected_wait,
    )
    with pytest.raises(AssertionError, match="viewer_reader_unavailable"):
        _index(page, SimpleNamespace(portal="http://fixture"), "file")


def test_native_index_waits_only_while_pending():
    responses = iter([{"status": "pending"}, {"status": "ready", "native": True}])
    waits = []
    page = SimpleNamespace(
        request=SimpleNamespace(get=lambda _: SimpleNamespace(ok=True, json=lambda: next(responses))),
        wait_for_timeout=waits.append,
    )
    assert _index(page, SimpleNamespace(portal="http://fixture"), "file")["native"]
    assert len(waits) == 1
