import fcntl
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
    monkeypatch.setattr(listener, "rss_bytes", lambda pid: 200 if reason == "rss" else 1)
    listener.serve(100)
    assert actions == ["terminate"]
    assert commands[0][0].endswith("/soffice.bin")
