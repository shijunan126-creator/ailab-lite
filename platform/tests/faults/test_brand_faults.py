"""Real parser death and a replacement racing first bootstrap preserve the old ready set."""

import threading
from concurrent.futures import ThreadPoolExecutor

from ailab_platform.worker import isolate
from ailab_platform.worker.pipeline import process_file
from tests.test_projects_brand import SERVICE, WorkerAPI, start, upload_brand
from tests.test_projects_brand import api as api


def test_parser_sigkill_never_activates_replacement(api, monkeypatch):
    first = upload_brand(api)
    process_file(first["file_id"], WorkerAPI(api))
    second = upload_brand(api)
    original = isolate.subprocess.Popen

    def killed(*args, **kwargs):
        proc = original(*args, **kwargs)
        proc.kill()
        proc.wait()
        return proc

    monkeypatch.setattr(isolate.subprocess, "Popen", killed)
    result = process_file(second["file_id"], WorkerAPI(api))
    assert result["rejected"] == "resource_limit"
    value = api.get("/v1/admin/brand").json()
    assert value["logo_file_id"] == first["file_id"]
    assert value["assets"]["logo"]["status"] == "rejected"


def test_replacement_while_run_starts_freezes_complete_version(api, monkeypatch):
    api.patch("/v1/admin/brand", json={"enabled": True})
    first = upload_brand(api)
    process_file(first["file_id"], WorkerAPI(api))
    second = upload_brand(api)
    entered, release = threading.Event(), threading.Event()
    worker = WorkerAPI(api)
    patch = worker.patch_file

    def held(file_id, **kwargs):
        if kwargs.get("status") == "ready":
            entered.set()
            assert release.wait(10)
        return patch(file_id, **kwargs)

    monkeypatch.setattr(worker, "patch_file", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(process_file, second["file_id"], worker)
        assert entered.wait(10)
        try:
            _, run = start(api)
            frozen = api.get(f"/internal/runs/{run['id']}", headers=SERVICE).json()["brand"]
            assert frozen["assets"]["logo"] == first["file_id"]
        finally:
            release.set()
        assert pending.result(timeout=10)["brand"]
    assert api.get(f"/internal/runs/{run['id']}", headers=SERVICE).json()["brand"] == frozen
    _, new_run = start(api)
    current = api.get(f"/internal/runs/{new_run['id']}", headers=SERVICE).json()["brand"]
    assert current["assets"]["logo"] == second["file_id"] and current["version"] > frozen["version"]


def test_move_failure_rolls_back_conversation_and_file(api, monkeypatch):
    import pytest

    from ailab_platform.api import routes_conversations
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import Conversation, FileObject
    from tests.run_claims import attempt_headers
    from tests.test_projects import artifact

    project = api.post("/v1/projects", json={"name": "Rollback destination"}).json()
    conv, run = start(api)
    api.post(f"/internal/runs/{run['id']}/status", headers=attempt_headers(api, run["id"]), json={"status": "running"})
    output = artifact(api, run["id"])
    original = routes_conversations.move_lineage

    def fail_after_update(db, file, project_id):
        original(db, file, project_id)
        raise RuntimeError("injected move failure")

    monkeypatch.setattr(routes_conversations, "move_lineage", fail_after_update)
    with pytest.raises(RuntimeError, match="injected move failure"):
        api.patch(f"/v1/conversations/{conv['id']}", json={"project_id": project["id"], "move_files": True})
    with SessionLocal() as db:
        assert db.get(Conversation, conv["id"]).project_id is None
        assert db.get(FileObject, output["file_id"]).project_id is None
