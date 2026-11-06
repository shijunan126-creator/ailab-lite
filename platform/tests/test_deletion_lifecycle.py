"""Deletion, restoration and late writers must converge across content and storage."""

from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.api.reaper import drain_object_deletions, purge_orphan_objects
from ailab_platform.api.routes_runs import _poll_events
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import ContextSummaryFrame, Conversation, FileObject, OutboxMessage, SkillDraft, utcnow
from ailab_platform.storage import get_storage
from tests.run_claims import SERVICE, attempt_headers
from tests.test_conversations import _finish
from tests.test_library import api as api
from tests.test_library import ready
from tests.test_personal_skills import SKILL_MD, _upload, _zip
from tests.test_skill_drafts import WorkerClient
from tests.test_skill_settings import _user


@pytest.mark.parametrize("target", ["conversation", "project"])
def test_deleted_conversation_hides_all_versions_but_keeps_library(api, target):
    project = api.post("/v1/projects", json={"name": uuid4().hex}).json()
    conv = api.post("/v1/conversations", json={"project_id": project["id"]}).json()
    first = api.post(f"/v1/conversations/{conv['id']}/messages", json={"prompt": "first"}).json()
    _finish(api, first["id"], "answer")
    old = api.post(f"/v1/conversations/{conv['id']}/messages", json={"prompt": "old"}).json()
    _finish(api, old["id"], "old answer")
    new = api.post(f"/v1/runs/{old['id']}/edit", json={"prompt": "new"}).json()
    _finish(api, new["id"], "new answer")
    file = ready(api, uuid4().hex + ".txt", b"independent bytes")
    other = api.post("/v1/runs", json={"prompt": "unrelated"}).json()
    for run in (first, old, new):
        get_storage().put_bytes(f"previews/{run['id']}/1/p-01.jpg", b"preview")
        assert api.get(f"/v1/runs/{run['id']}").status_code == 200
    url = f"/v1/conversations/{conv['id']}" if target == "conversation" else f"/v1/projects/{project['id']}"
    assert api.delete(url).status_code == 204
    for run in (first, old, new):
        rid = run["id"]
        for suffix in ("", "/events/history", "/process", "/previews/1/p-01.jpg", "/events"):
            assert api.get(f"/v1/runs/{rid}{suffix}").status_code == 404
        for suffix in ("", "/model-input", "/model-context"):
            assert api.get(f"/internal/runs/{rid}{suffix}", headers=SERVICE).status_code == 404
        assert _poll_events(rid, 0) == ([], "canceled", False)
    listed = {row["id"] for row in api.get("/v1/runs").json()}
    assert not listed.intersection({r["id"] for r in (first, old, new)})
    assert other["id"] in listed
    assert api.get(f"/v1/files/{file['id']}/content").content == b"independent bytes"


@pytest.mark.parametrize("stop", ["delete", "cancel"])
def test_late_summary_cannot_write_after_stop(api, stop):
    conv = api.post("/v1/conversations", json={}).json()
    first = api.post(f"/v1/conversations/{conv['id']}/messages", json={"prompt": "first"}).json()
    _finish(api, first["id"], "answer")
    next_run = api.post(f"/v1/conversations/{conv['id']}/messages", json={"prompt": "next"}).json()
    headers = attempt_headers(api, next_run["id"])
    assert api.post(f"/internal/runs/{next_run['id']}/status", headers=headers, json={"status": "running"}).status_code == 200
    context = api.get(f"/internal/runs/{next_run['id']}/model-context", headers=SERVICE).json()
    if stop == "delete":
        assert api.delete(f"/v1/conversations/{conv['id']}").status_code == 204
    else:
        assert api.post(f"/v1/runs/{next_run['id']}/cancel").status_code == 200
    response = api.put(
        f"/internal/runs/{next_run['id']}/model-summary",
        headers=headers,
        json={
            "summary": '{"schema_version":1,"objective":"late summary"}',
            "through_run_id": first["id"],
            "expected_through_run_id": None,
            "source_epoch": context["source_epoch"],
        },
    )
    assert response.status_code == 409, response.text
    with SessionLocal() as db:
        assert db.get(Conversation, conv["id"]).summary is None
        assert not db.scalar(select(ContextSummaryFrame.id).where(ContextSummaryFrame.conversation_id == conv["id"]))


@pytest.mark.parametrize("restore", ["single", "batch"])
@pytest.mark.parametrize("claimed", [False, True])
def test_restore_replaces_scan_job_and_rejects_old_completion(api, restore, claimed):
    f = api.post("/v1/files?disposition=copy", files={"upload": (uuid4().hex + ".txt", b"scan me")}).json()
    with SessionLocal() as db:
        old_job = db.scalar(select(OutboxMessage.id).where(OutboxMessage.payload["file_id"].as_string() == f["id"]))
    old_token = "obsolete"
    if claimed:
        old_token = api.post(f"/internal/jobs/{old_job}/claim", headers=SERVICE, json={"consumer": "old"}).json()["token"]
    assert api.delete(f"/v1/files/{f['id']}").status_code == 204
    if not claimed:
        assert api.post(f"/internal/jobs/{old_job}/claim", headers=SERVICE, json={"consumer": "old"}).json() == {"state": "done"}
    for _ in range(2):
        response = (
            api.post(f"/v1/files/{f['id']}/restore")
            if restore == "single"
            else api.post("/v1/files/batch", json={"ids": [f["id"]], "action": "restore"})
        )
        assert response.status_code == 200, response.text
    with SessionLocal() as db:
        job_id = db.get(FileObject, f["id"]).meta["processing_job_id"]
        assert job_id != old_job
        assert db.get(OutboxMessage, job_id) is not None
    if claimed:
        # Even if the old queue delivery is retried after restoration, it is obsolete.
        assert (
            api.patch(f"/internal/files/{f['id']}", headers={**SERVICE, "X-Run-Attempt": old_token}, json={"status": "ready"}).status_code
            == 409
        )
    for endpoint, body in [
        ("", {"status": "ready"}),
        ("/prepare-document", {"scan": {"verdict": "clean"}}),
        ("/reuse-document", {"scan": {"verdict": "clean"}}),
    ]:
        method = api.patch if not endpoint else api.post
        assert method(f"/internal/files/{f['id']}{endpoint}", headers={**SERVICE, "X-Run-Attempt": old_token}, json=body).status_code == 409
    claim = api.post(f"/internal/jobs/{job_id}/claim", headers=SERVICE, json={"consumer": "restored"}).json()
    assert claim["state"] == "claimed"
    assert (
        api.patch(f"/internal/files/{f['id']}", headers={**SERVICE, "X-Run-Attempt": claim["token"]}, json={"status": "ready"}).status_code
        == 200
    )
    assert api.get(f"/v1/files/{f['id']}/content").content == b"scan me"


def test_restore_preview_rebuilds_only_deletion_failure(api):
    f = ready(api, uuid4().hex + ".txt", b"preview me")
    assert api.get(f"/v1/files/{f['id']}/preview").json()["status"] == "pending"
    with SessionLocal() as db:
        old_job = db.get(FileObject, f["id"]).meta["preview_job_id"]
    assert api.delete(f"/v1/files/{f['id']}").status_code == 204
    assert api.post(f"/v1/files/{f['id']}/restore").status_code == 200
    assert api.get(f"/v1/files/{f['id']}/preview").json()["status"] == "pending"
    with SessionLocal() as db:
        new_job = db.get(FileObject, f["id"]).meta["preview_job_id"]
    assert new_job != old_job
    from tests.test_file_previews import ApiClient

    worker = ApiClient(api)
    assert worker.start_file_work(new_job)["status"] == "running"
    assert (
        api.post(
            f"/internal/files/{f['id']}/preview",
            headers=SERVICE,
            json={"job_id": old_job, "preview": {"status": "failed", "reason": "late"}, "assets": {}},
        ).status_code
        == 409
    )
    assert (
        api.post(
            f"/internal/files/{f['id']}/preview",
            headers=worker.headers,
            json={"job_id": new_job, "preview": {"status": "failed", "reason": "parser_error"}, "assets": {}},
        ).status_code
        == 200
    )
    api.delete(f"/v1/files/{f['id']}")
    api.post(f"/v1/files/{f['id']}/restore")
    assert api.get(f"/v1/files/{f['id']}/preview").json()["reason"] == "parser_error"


@pytest.mark.parametrize("zipped", [False, True])
@pytest.mark.parametrize("action", ["skill", "trash"])
def test_skill_deletion_invalidates_dependent_unconfirmed_drafts(zipped, action):
    with TestClient(app) as c:
        me = _user(c)
        name = "delete-" + uuid4().hex[:8]
        content = SKILL_MD.format(name=name).encode()
        fid = _upload(c, me, "skill.zip" if zipped else "skill.md", _zip({"SKILL.md": content}) if zipped else content)
        d = c.post(f"/v1/skills/{name}/drafts", headers=me).json()
        if not zipped:
            response = c.put(
                f"/v1/skill-drafts/{d['id']}/files/SKILL.md",
                headers=me,
                json={"content": content.decode() + "\nmodified", "expected_revision": d["revision"]},
            )
            assert response.status_code == 200
        url = f"/v1/skills/files/{fid}" if action == "skill" else f"/v1/files/{fid}"
        assert c.delete(url, headers=me).status_code == 204
        d = c.get(f"/v1/skill-drafts/{d['id']}", headers=me).json()
        assert d["status"] == "discarded" and not d["files"] and d["name"] is None
        assert c.get(f"/v1/skill-drafts/{d['id']}/files/SKILL.md", headers=me).status_code == 404


def test_late_unpack_and_crashed_attempt_objects_are_collected(monkeypatch):
    from ailab_platform.api import reaper
    from ailab_platform.worker import skill_unpack

    with TestClient(app) as c:
        me = _user(c)
        name = "late-" + uuid4().hex[:8]
        _upload(c, me, "skill.zip", _zip({"SKILL.md": SKILL_MD.format(name=name).encode(), "private.txt": b"deleted"}))
        draft = c.post(f"/v1/skills/{name}/drafts", headers=me).json()
        did = draft["id"]
        with SessionLocal() as db:
            row = db.get(SkillDraft, did)
            prefix = f"skill-drafts/{row.user_id}/{did}/"
            job_id = row.claim_token
        claim = c.post(f"/internal/jobs/{job_id}/claim", headers=SERVICE, json={"consumer": "late"}).json()
        real_execute = skill_unpack.execute

        def discard_after_unpack(*args, **kwargs):
            result = real_execute(*args, **kwargs)
            assert c.delete(f"/v1/skill-drafts/{did}", headers=me, params={"expected_revision": draft["revision"]}).status_code == 200
            with SessionLocal() as db:
                for _ in range(10):
                    drain_object_deletions(db, get_storage())
            assert not get_storage().list_prefix(prefix).items
            return result

        monkeypatch.setattr(skill_unpack, "execute", discard_after_unpack)
        with pytest.raises(httpx.HTTPStatusError) as caught:
            skill_unpack.process_skill_unpack(WorkerClient(c), did, job_id, claim["token"])
        assert caught.value.response.status_code == 409
        keys = [item.key for item in get_storage().list_prefix(prefix)]
        assert len(keys) == 2 and all(f"/attempts/{claim['token']}/" in k for k in keys)
        monkeypatch.setattr(settings, "orphan_objects_grace_seconds", 1)
        reaper._gc_cursors.clear()
        with SessionLocal() as db:
            purge_orphan_objects(db, get_storage(), utcnow() + timedelta(days=2))
        assert not any(get_storage().exists(k) for k in keys)
