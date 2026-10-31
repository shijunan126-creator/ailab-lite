import sys
from types import SimpleNamespace

import pytest

from ailab_platform.sandbox_tools import office_exec
from ailab_platform.sandbox_tools.office_exec import run
from ailab_platform.worker import office
from ailab_platform.worker.isolate import IsolationError


def test_reader_process_is_killed_when_over_rss_limit():
    assert run([sys.executable, "-c", "import time; data = bytearray(24_000_000); time.sleep(5)"], 3, 8 * 1024**2) == 137


def test_reader_process_is_killed_on_deadline():
    assert run([sys.executable, "-c", "import time; time.sleep(5)"], 0.1, 512 * 1024**2) == 124


def test_exiting_reader_without_memory_map_keeps_its_exit_code(monkeypatch):
    # On Linux an exiting process has no VmRSS before it can be reaped; that window is not a parser failure.
    def unreadable(pid):
        raise OSError("no memory map")

    monkeypatch.setattr(office_exec, "rss", unreadable)
    assert run([sys.executable, "-c", "import time; time.sleep(0.2)"], 3, 512 * 1024**2) == 0


def test_rss_of_a_process_without_vmrss_is_zero(monkeypatch, tmp_path):
    status = tmp_path / "status"
    status.write_text("Name:\tnode\nState:\tX (dead)\n")
    monkeypatch.setattr(office_exec.sys, "platform", "linux")
    monkeypatch.setattr(office_exec, "Path", lambda path: status)
    assert office_exec.rss(1) == 0


def test_invalid_document_output_discards_lease_without_deleting_untrusted_key(tmp_path):
    finished = []
    platform = SimpleNamespace(
        prepare_document=lambda *args: {"job_id": "job"},
        read_document=lambda *args: {
            "archive_key": "other-user/file",
            "bytes": 1,
            "render_lease_id": "lease",
            "render_generation": "generation",
        },
        finish_render=lambda *args: finished.append(args),
    )
    storage = SimpleNamespace(delete=lambda key: pytest.fail("must not delete an untrusted key"))
    with pytest.raises(IsolationError, match="invalid_document_output"):
        office.read("file", platform, storage, tmp_path, {"verdict": "clean"})
    assert finished == [("lease", "job", "document", "generation", False)]
