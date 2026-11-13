"""Broker: a conversation's workspace survives between turns; office files in outputs get previews."""

from __future__ import annotations

import io
import json

import pytest
from fastapi import HTTPException
from openpyxl import Workbook

from ailab_platform.adapters.lease_store_memory import MemoryLeaseStore
from ailab_platform.broker.service import LeaseManager
from ailab_platform.sandbox.mock import MockSandbox
from ailab_platform.storage import LocalStorage

from .fakes import FakeRedis


class _Api:
    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}
        self.assets = {}

    def reserve_derived(self, **body):
        from uuid import uuid4

        key = body["key"]
        grant = {"key": key, "token": uuid4().hex, "state": "writing", "version": body["version"], "max_bytes": body["max_bytes"]}
        self.assets[key] = grant
        return grant

    def finish_derived(self, key, token):
        assert self.assets[key]["token"] == token
        self.assets[key]["state"] = "staged"

    def reserve_derived_batch(self, items):
        return {"items": [self.reserve_derived(**item) for item in items]}

    def finish_derived_batch(self, items):
        for item in items:
            self.finish_derived(item["key"], item["token"])

    def publish_run_derived(self, items):
        for item in items:
            assert self.assets[item["key"]]["token"] == item["token"]
            self.assets[item["key"]]["state"] = "ready"

    def derived_ready(self, key, version):
        row = self.assets.get(key, {})
        return row.get("state") == "ready" and row.get("version") == version

    def abandon_derived_batch(self, items):
        return {"deletable": [item for item in items if self.assets.get(item["key"], {}).get("state") != "ready"]}

    def discard_derived_batch(self, items):
        for item in items:
            self.assets.pop(item["key"], None)

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


@pytest.mark.parametrize("extension", ["docx", "xlsx", "pptx", "pdf"])
def test_discovery_announces_sources_without_rendering_or_prewarming(tmp_path, monkeypatch, extension):
    manager, _, _ = _manager(tmp_path)
    lease = manager.create("run_p")["lease_id"]
    assert manager.refresh_previews(lease) == []
    path = f"/workspace/outputs/source.{extension}"
    manager.put_file(lease, path, b"unfinished document")
    calls = []
    original = manager._provider_call

    def record(method, *args, **kwargs):
        calls.append((method, args))
        return original(method, *args, **kwargs)

    monkeypatch.setattr(manager, "_provider_call", record)
    monkeypatch.setattr(manager, "request_prewarm", lambda: pytest.fail("discovery must not prewarm render sandboxes"))
    [first] = manager.refresh_previews(lease)
    assert first == {"filename": f"source.{extension}", "path": path, "kind": "native", "version": 1}
    assert manager.refresh_previews(lease) == []
    assert not any(method in {"put_file", "get_file", "get_file_to_path"} for method, _ in calls)
    assert not any("render_preview" in str(args) for _, args in calls)
    assert not list((tmp_path / "store").rglob("*.pdf"))
    manager.put_file(lease, path, b"changed document")
    assert manager.refresh_previews(lease)[0]["version"] == 2


def test_fallback_renders_once_per_content_and_refreshes_after_change(tmp_path, monkeypatch):
    from ailab_platform.broker.viewer import workspace

    manager, _, storage = _manager(tmp_path)
    lease = manager.create("run_p")["lease_id"]
    path = "/workspace/outputs/sales.xlsx"
    manager.put_file(lease, path, _xlsx(100))
    manager.refresh_previews(lease)
    first = workspace(manager, "run_p", "user_test", "outputs/sales.xlsx", "preview")
    assert first["kind"] == "sheets" and first["version"] == 2
    data = json.loads(storage.get_bytes(f"previews/run_p/{first['data']}"))
    cells = [[c["v"] for c in row] for row in data["sheets"][0]["rows"]]
    assert cells[1] == ["华东", "100"]
    original = manager._render_preview
    monkeypatch.setattr(manager, "_render_preview", lambda *_: pytest.fail("unchanged fallback rendered twice"))
    prewarm = manager.request_prewarm
    monkeypatch.setattr(manager, "request_prewarm", lambda: pytest.fail("cached fallback must not prewarm"))
    assert workspace(manager, "run_p", "user_test", "outputs/sales.xlsx", "preview") == first
    monkeypatch.setattr(manager, "_render_preview", original)
    monkeypatch.setattr(manager, "request_prewarm", prewarm)
    manager.put_file(lease, path, _xlsx(250))
    second = workspace(manager, "run_p", "user_test", "outputs/sales.xlsx", "preview")
    assert second["version"] == 3 and second["data"] != first["data"]
    # Immutable old assets retain the first version's values.
    assert json.loads(storage.get_bytes(f"previews/run_p/{first['data']}")) == data
    with pytest.raises(HTTPException) as error:
        workspace(manager, "run_p", "other-user", "outputs/sales.xlsx", "preview")
    assert error.value.status_code == 410
    with pytest.raises(HTTPException) as error:
        workspace(manager, "run_p", "user_test", "../sales.xlsx", "preview")
    assert error.value.status_code == 422


def test_failed_fallback_releases_temporary_files_and_keeps_discovery_usable(tmp_path, monkeypatch):
    from ailab_platform.broker.viewer import workspace

    manager, _, _ = _manager(tmp_path)
    lease = manager.create("run_e")["lease_id"]
    manager.put_file(lease, "/workspace/outputs/sales.xlsx", b"invalid workbook")
    failed = workspace(manager, "run_e", "user_test", "outputs/sales.xlsx", "preview")
    assert failed["error"]
    assert not any("/.ailab/viewer-" in f["path"] or "/.ailab/preview/" in f["path"] for f in manager.list_files(lease, "/workspace"))
    assert manager.refresh_previews(lease)[0]["kind"] == "native"


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
    from ailab_platform.broker.viewer import workspace

    preview = workspace(manager, "run_pdf", "user_test", "outputs/report.pdf", "preview")
    assert preview["kind"] == "pdf" and preview["pdf"].endswith("/document.pdf")
    assert storage.get_bytes(f"previews/run_pdf/{preview['pdf']}").startswith(b"%PDF")
