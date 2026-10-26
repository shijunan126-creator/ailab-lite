"""Broker: a conversation's workspace survives between turns; office files in outputs get previews."""

from __future__ import annotations

import io
import json
import shutil

from openpyxl import Workbook

from ailab_platform.adapters.lease_store_memory import MemoryLeaseStore
from ailab_platform.broker.service import LeaseManager
from ailab_platform.sandbox.mock import MockSandbox
from ailab_platform.storage import LocalStorage

from .fakes import FakeRedis


class _Api:
    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}

    def get_run(self, run_id):
        return {"id": run_id, "user_id": "user_test", "input_file_ids": [], **self.runs.get(run_id, {})}

    def append_event(self, *_a, **_k):
        return {}


def _manager(tmp_path):
    api = _Api()
    storage = LocalStorage(tmp_path / "store")
    provider = MockSandbox()
    provider.root = tmp_path / "sandboxes"
    provider.root.mkdir()
    return (
        LeaseManager(
            provider=provider, platform=api, storage=storage, redis_client=FakeRedis(), store=MemoryLeaseStore(), start_background=False
        ),
        api,
        storage,
    )


def _xlsx(value: int) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["区域", "金额"])
    ws.append(["华东", value])
    ws.append(["合计", "=SUM(B2:B2)"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_the_workspace_comes_back_in_the_next_turn_without_inputs_or_platform_files(tmp_path) -> None:
    manager, api, storage = _manager(tmp_path)
    first = manager.create("run_1")
    manager.put_file(first["lease_id"], "/workspace/work/deck.json", b'{"slides": []}')
    manager.put_file(first["lease_id"], "/workspace/outputs/deck.pptx", b"PK fake")
    manager.put_file(first["lease_id"], "/workspace/inputs/upload.txt", b"user upload")
    saved = manager.snapshot(first["lease_id"])
    # every snapshot is its own immutable object, so a checkpoint's archive is never overwritten by a later one
    assert saved["key"].startswith("workspaces/run_1/") and saved["key"].endswith(".tgz") and storage.exists(saved["key"])
    manager.release(first["lease_id"])

    api.runs["run_2"] = {"workspace_restore_key": saved["key"]}
    second = manager.create("run_2")
    assert second["restored"] is True
    files = {f["path"] for f in manager.list_files(second["lease_id"], "/workspace")}
    assert {"/workspace/work/deck.json", "/workspace/outputs/deck.pptx"} <= files
    assert "/workspace/inputs/upload.txt" not in files  # inputs belong to the turn that uploaded them
    assert not any("/.ailab/" in f for f in files)
    assert manager.get_file(second["lease_id"], "/workspace/work/deck.json") == b'{"slides": []}'


def test_a_broken_snapshot_costs_continuity_not_the_turn(tmp_path) -> None:
    manager, api, storage = _manager(tmp_path)
    storage.put_bytes("workspaces/run_0.tgz", b"not a tarball")
    api.runs["run_1"] = {"workspace_restore_key": "workspaces/run_0.tgz"}
    lease = manager.create("run_1")
    assert lease["restored"] is False and lease["restore_error"]
    assert manager.exec(lease["lease_id"], "echo ok").stdout.strip() == "ok"


def test_previews_are_rendered_once_per_change(tmp_path) -> None:
    manager, _, storage = _manager(tmp_path)
    lease = manager.create("run_p")["lease_id"]
    assert manager.refresh_previews(lease) == []

    manager.put_file(lease, "/workspace/outputs/sales.xlsx", _xlsx(100))
    [first] = manager.refresh_previews(lease)
    assert first["filename"] == "sales.xlsx" and first["kind"] == "sheets" and first["version"] == 1
    data = json.loads(storage.get_bytes(f"previews/run_p/{first['data']}"))
    cells = [[c["v"] for c in row] for row in data["sheets"][0]["rows"]]
    assert cells[1] == ["华东", "100"]
    assert manager.refresh_previews(lease) == []  # unchanged: nothing to do

    manager.put_file(lease, "/workspace/outputs/sales.xlsx", _xlsx(250) + b"")
    manager.exec(lease, "touch -d '+1 minute' /workspace/outputs/sales.xlsx")
    [second] = manager.refresh_previews(lease)
    assert second["version"] == 2
    assert not storage.exists("workspaces/run_p.tgz")


def test_a_preview_that_cannot_be_rendered_is_reported_not_retried(tmp_path, monkeypatch) -> None:
    # Keep the GNU find selected by the test environment (on macOS it is outside /usr/bin), while excluding LibreOffice.
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "find").symlink_to(shutil.which("find"))
    monkeypatch.setenv("PATH", f"{tools}:/usr/bin:/bin")
    manager, _, _ = _manager(tmp_path)
    lease = manager.create("run_e")["lease_id"]
    manager.put_file(lease, "/workspace/outputs/deck.pptx", b"not really a deck")
    [failed] = manager.refresh_previews(lease)
    assert failed["filename"] == "deck.pptx" and failed["error"]
    assert manager.refresh_previews(lease) == []


def test_preview_cache_update_preserves_concurrent_heartbeat(tmp_path, monkeypatch) -> None:
    manager, _, _ = _manager(tmp_path)
    lease = manager.create("run-heartbeat")
    lid = lease["lease_id"]
    manager.put_file(lid, "/workspace/outputs/data.xlsx", _xlsx(1))
    fingerprints = manager._fingerprints
    renewed_expiry = []

    def heartbeat_during_render(ref):
        renewed_expiry.append(manager.renew(lid)["expires_at"])
        return fingerprints(ref)

    monkeypatch.setattr(manager, "_fingerprints", heartbeat_during_render)
    assert manager.refresh_previews(lid)[0]["version"] == 1
    assert manager.get(lid)["expires_at"] == renewed_expiry[0]


def test_office_previews_are_kept_as_a_pdf_for_pdfjs(tmp_path) -> None:
    from pypdf import PdfWriter

    manager, _, storage = _manager(tmp_path)
    lease = manager.create("run_pdf")["lease_id"]
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    payload = io.BytesIO()
    writer.write(payload)
    manager.put_file(lease, "/workspace/outputs/report.pdf", payload.getvalue())
    [preview] = manager.refresh_previews(lease)
    assert preview["kind"] == "pdf" and preview["pdf"].endswith("/document.pdf")
    assert storage.get_bytes(f"previews/run_pdf/{preview['pdf']}").startswith(b"%PDF")
