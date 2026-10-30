import asyncio
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import Conversation, Notification, OutboxMessage, Run
from ailab_platform.skill_drafts import service
from ailab_platform.worker.skill_unpack import process_skill_unpack

from .test_personal_skills import SKILL_MD, _upload, _zip
from .test_skill_settings import SERVICE, _user


def _conversation(c, user):
    user_id = c.get("/v1/me", headers=user).json()["id"]
    conv = c.post("/v1/conversations", headers=user, json={}).json()["id"]
    with SessionLocal() as db:
        run = Run(user_id=user_id, conversation_id=conv, prompt="核对结果", status="succeeded", summary="已核对数据", model="")
        db.add(run)
        db.flush()
        db.get(Conversation, conv).active_run_id = run.id
        db.commit()
    return conv


class WorkerClient:
    def __init__(self, client):
        self.client = client

    def skill_draft_action(self, draft, action, **body):
        response = self.client.post(f"/internal/skill-drafts/{draft}/{action}", headers=SERVICE, json=body)
        response.raise_for_status()
        return response.json()


def test_edit_confirm_and_conflicts():
    with TestClient(app) as c:
        me, other = _user(c), _user(c)
        name = "draft-example"
        _upload(c, me, "skill.md", SKILL_MD.format(name=name).encode())
        opened = c.post(f"/v1/skills/{name}/drafts", headers=me, json={})
        assert opened.status_code == 202
        draft = opened.json()
        assert draft["status"] == "ready" and draft["base"]["kind"] == "personal"
        did = draft["id"]
        assert c.get(f"/v1/skill-drafts/{did}", headers=other).status_code == 404
        url = f"/v1/skill-drafts/{did}/files/reference/a.txt"
        assert c.put(url, headers=me, json={"content": "hello", "expected_revision": 0}).status_code == 200
        assert c.put(url, headers=me, json={"content": "stale", "expected_revision": 0}).status_code == 409
        moved = c.post(f"/v1/skill-drafts/{did}/move", headers=me, json={"from": "reference", "to": "notes", "expected_revision": 1})
        assert moved.status_code == 200
        assert c.get(f"/v1/skill-drafts/{did}/files/notes/a.txt", headers=me).text == "hello"
        assert c.delete(f"/v1/skill-drafts/{did}/files/notes?expected_revision=2", headers=me).status_code == 200
        response = c.post(f"/v1/skill-drafts/{did}/confirm", headers=me, json={"expected_revision": 3})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["version"] == 2
        assert c.post(f"/v1/skill-drafts/{did}/confirm", headers=me, json={"expected_revision": 3}).json() == result
        assert c.post(f"/v1/skill-drafts/{did}/confirm", headers=other, json={"expected_revision": 3}).status_code == 404


def test_worker_unpack_redelivery_and_security():
    with TestClient(app) as c:
        me = _user(c)
        name = "zip-draft"
        _upload(c, me, "skill.zip", _zip({"SKILL.md": SKILL_MD.format(name=name).encode(), "scripts/a.py": b"print(1)"}))
        draft = c.post(f"/v1/skills/{name}/drafts", headers=me, json={}).json()
        assert draft["status"] == "preparing"
        with SessionLocal() as db:
            job = db.scalar(select(OutboxMessage).where(OutboxMessage.payload["draft_id"].as_string() == draft["id"]))
            job_id = job.id
        claim = c.post(f"/internal/jobs/{job_id}/claim", headers=SERVICE, json={"consumer": "test"}).json()
        assert claim["state"] == "claimed"
        assert (
            c.post(f"/internal/skill-drafts/{draft['id']}/source", headers=SERVICE, json={"job_id": job_id, "token": "wrong"}).status_code
            == 409
        )
        process_skill_unpack(WorkerClient(c), draft["id"], job_id, claim["token"])
        result = c.get(f"/v1/skill-drafts/{draft['id']}", headers=me).json()
        assert result["status"] == "ready" and {f["path"] for f in result["files"]} == {"SKILL.md", "scripts/a.py"}
        assert c.post(f"/internal/jobs/{job_id}/ack", headers=SERVICE, json={"token": claim["token"]}).status_code == 200
        assert c.post(f"/internal/jobs/{job_id}/claim", headers=SERVICE, json={"consumer": "test"}).json()["state"] == "done"


def test_extract_create_and_deletion_fence(monkeypatch):
    with TestClient(app) as c:
        me = _user(c)
        conv = _conversation(c, me)
        did = c.post(f"/v1/conversations/{conv}/skill-drafts", headers=me, json={}).json()["id"]

        async def fake_chat(messages, model, user_id, draft_id, token):
            assert messages[0]["role"] == "system" and "核对结果" in messages[1]["content"]
            return json.dumps(
                {
                    "action": "create",
                    "files": [{"path": "SKILL.md", "content": SKILL_MD.format(name="extract-test")}],
                    "summary": "整理方法",
                }
            )

        monkeypatch.setattr(service, "_chat", fake_chat)
        asyncio.run(service.process_draft(did))
        draft = c.get(f"/v1/skill-drafts/{did}", headers=me).json()
        assert draft["status"] == "ready" and draft["name"] == "extract-test"
        with SessionLocal() as db:
            notification = db.scalar(select(Notification).where(Notification.conversation_id == conv))
            assert notification.kind == "skill_draft.ready" and notification.title == (draft["title"] or "技能草稿")
        c.delete(f"/v1/conversations/{conv}", headers=me)
        assert c.get(f"/v1/skill-drafts/{did}", headers=me).json()["status"] == "discarded"


def test_invalid_model_output_can_retry(monkeypatch):
    with TestClient(app) as c:
        me = _user(c)
        conv = _conversation(c, me)
        did = c.post(f"/v1/conversations/{conv}/skill-drafts", headers=me, json={}).json()["id"]

        async def bad(*args):
            return "not json"

        monkeypatch.setattr(service, "_chat", bad)
        asyncio.run(service.process_draft(did))
        assert c.get(f"/v1/skill-drafts/{did}", headers=me).json()["status"] == "failed"
        with SessionLocal() as db:
            notification = db.scalar(select(Notification).where(Notification.conversation_id == conv))
            assert notification.kind == "skill_draft.failed" and notification.title == "技能提取失败"
        assert c.post(f"/v1/skill-drafts/{did}/retry?expected_revision=0", headers=me).json()["status"] == "queued"
        assert c.delete(f"/v1/skill-drafts/{did}?expected_revision=1", headers=me).json()["status"] == "discarded"


def test_extraction_update_merges_zip_and_second_call_reads_base(monkeypatch):
    with TestClient(app) as c:
        me = _user(c)
        name = "merge-example"
        _upload(c, me, "skill.zip", _zip({"SKILL.md": SKILL_MD.format(name=name).encode(), "scripts/a.py": b"original"}))
        conv = _conversation(c, me)
        did = c.post(f"/v1/conversations/{conv}/skill-drafts", headers=me, json={}).json()["id"]
        calls = []

        async def chat(messages, model, user_id, draft_id, token):
            dynamic = json.loads(messages[1]["content"])
            calls.append(dynamic)
            if len(calls) == 2:
                assert "运行 scripts/fill.py" in dynamic["target_skill_md"]
                assert {f["path"] for f in dynamic["target_files"]} == {"SKILL.md", "scripts/a.py"}
            return json.dumps(
                {"action": "update", "target": name, "files": [{"path": "notes.txt", "content": "补充"}], "summary": "补充步骤"}
            )

        monkeypatch.setattr(service, "_chat", chat)
        asyncio.run(service.process_draft(did))
        assert c.get(f"/v1/skill-drafts/{did}", headers=me).json()["status"] == "preparing"
        with SessionLocal() as db:
            job = db.scalar(select(OutboxMessage).where(OutboxMessage.payload["draft_id"].as_string() == did))
            job_id = job.id
        claim = c.post(f"/internal/jobs/{job_id}/claim", headers=SERVICE, json={"consumer": "test"}).json()
        process_skill_unpack(WorkerClient(c), did, job_id, claim["token"])
        assert c.get(f"/v1/skill-drafts/{did}", headers=me).json()["status"] == "queued"
        asyncio.run(service.process_draft(did))
        draft = c.get(f"/v1/skill-drafts/{did}", headers=me).json()
        assert draft["status"] == "ready" and len(calls) == 2
        assert {f["path"]: f["change"] for f in draft["files"]} == {
            "SKILL.md": "unchanged",
            "scripts/a.py": "unchanged",
            "notes.txt": "added",
        }
        result = c.post(f"/v1/skill-drafts/{did}/confirm", headers=me, json={"expected_revision": draft["revision"]})
        assert result.status_code == 200 and result.json()["version"] == 2


def test_source_deleted_during_model_await_without_user_lock(monkeypatch):
    with TestClient(app) as c:
        me = _user(c)
        conv = _conversation(c, me)
        did = c.post(f"/v1/conversations/{conv}/skill-drafts", headers=me, json={}).json()["id"]

        async def chat(*args):
            response = await asyncio.wait_for(asyncio.to_thread(c.delete, f"/v1/conversations/{conv}", headers=me), timeout=3)
            assert response.status_code == 204
            return json.dumps(
                {"action": "create", "files": [{"path": "SKILL.md", "content": SKILL_MD.format(name="deleted-test")}], "summary": "x"}
            )

        monkeypatch.setattr(service, "_chat", chat)
        asyncio.run(service.process_draft(did))
        draft = c.get(f"/v1/skill-drafts/{did}", headers=me).json()
        assert draft["status"] == "discarded" and draft["files"] == []


def test_upload_content_security_and_version_conflict():
    with TestClient(app) as c:
        me = _user(c)
        name = "conflict-example"
        _upload(c, me, "skill.md", SKILL_MD.format(name=name).encode())
        first = c.post(f"/v1/skills/{name}/drafts", headers=me, json={}).json()
        reused = c.post(f"/v1/skills/{name}/drafts", headers=me, json={})
        assert reused.status_code == 200 and reused.json()["id"] == first["id"]
        did = first["id"]
        url = f"/v1/skill-drafts/{did}/files/image.svg"
        put = c.put(
            url, headers=me, files={"file": ("image.svg", b"<svg onload='alert(1)'/>", "image/svg+xml")}, data={"expected_revision": "0"}
        )
        assert put.status_code == 200
        fetched = c.get(url, headers=me)
        assert fetched.headers["content-disposition"] == "attachment"
        assert fetched.headers["x-content-type-options"] == "nosniff"
        assert c.put(f"/v1/skill-drafts/{did}/files/a%5Cb", headers=me, json={"content": "x", "expected_revision": 1}).status_code == 422
        assert (
            c.put(
                f"/v1/skill-drafts/{did}/files/big.txt", headers=me, json={"content": "x" * (512 * 1024 + 1), "expected_revision": 1}
            ).status_code
            == 413
        )
        assert c.post(f"/v1/skill-drafts/{did}/confirm", headers=me, json={"expected_revision": 1}).status_code == 200


def test_model_call_has_draft_usage_identity(monkeypatch):
    from ailab_platform.llm import LLMResponse
    from ailab_platform.models import ModelUsage

    class Model:
        async def chat(self, messages, tools, model, user_id=None, max_tokens=None):
            return LLMResponse(
                content=json.dumps(
                    {
                        "action": "create",
                        "files": [{"path": "SKILL.md", "content": SKILL_MD.format(name="metered-test")}],
                        "summary": "方法",
                    }
                ),
                usage={"prompt_tokens": 200, "completion_tokens": 100, "cached_tokens": 20},
            )

    monkeypatch.setattr(service, "llm_for", lambda model: Model())
    with TestClient(app) as c:
        me = _user(c)
        conv = _conversation(c, me)
        did = c.post(f"/v1/conversations/{conv}/skill-drafts", headers=me, json={}).json()["id"]
        asyncio.run(service.process_draft(did))
        assert c.get(f"/v1/skill-drafts/{did}", headers=me).json()["status"] == "ready"
        with SessionLocal() as db:
            usage = db.scalar(select(ModelUsage).where(ModelUsage.job_id == did))
            assert usage.subject == "skill_draft" and usage.prompt_tokens == 200 and usage.cached_tokens == 20


def test_worker_dispatch_ack_and_redelivery(monkeypatch):
    from ailab_platform.config import settings
    from ailab_platform.queue import InMemoryQueue
    from ailab_platform.worker import main

    with TestClient(app) as c:
        me = _user(c)
        name = "redelivery-example"
        _upload(c, me, "skill.zip", _zip({"SKILL.md": SKILL_MD.format(name=name).encode(), "data/a": b"ok"}))
        draft = c.post(f"/v1/skills/{name}/drafts", headers=me, json={}).json()
        with SessionLocal() as db:
            job = db.scalar(select(OutboxMessage).where(OutboxMessage.payload["draft_id"].as_string() == draft["id"]))
            payload, job_id = job.payload, job.id

        class Client(WorkerClient):
            def __init__(self, **kwargs):
                super().__init__(c)

            def job_action(self, job_id, action, **body):
                response = c.post(f"/internal/jobs/{job_id}/{action}", headers=SERVICE, json=body)
                response.raise_for_status()
                return response.json()

            def close(self):
                pass

        monkeypatch.setattr(main, "PlatformClient", Client)
        queue = InMemoryQueue()
        queue.enqueue(settings.file_queue, payload)
        main.handle_message(queue, queue.dequeue(settings.file_queue, timeout=0))
        queue.enqueue(settings.file_queue, payload)
        main.handle_message(queue, queue.dequeue(settings.file_queue, timeout=0))
        assert queue.length(settings.file_queue) == 0
        assert c.get(f"/v1/skill-drafts/{draft['id']}", headers=me).json()["status"] == "ready"
        with SessionLocal() as db:
            assert db.get(OutboxMessage, job_id) is None


def test_retry_unpack_fences_old_job_and_discard_removes_files():
    with TestClient(app) as c:
        me = _user(c)
        name = "retry-archive"
        _upload(c, me, "skill.zip", _zip({"SKILL.md": SKILL_MD.format(name=name).encode(), "a": b"x"}))
        did = c.post(f"/v1/skills/{name}/drafts", headers=me, json={}).json()["id"]
        with SessionLocal() as db:
            old = db.scalar(select(OutboxMessage).where(OutboxMessage.payload["draft_id"].as_string() == did))
            old_id = old.id
        claim = c.post(f"/internal/jobs/{old_id}/claim", headers=SERVICE, json={"consumer": "old"}).json()
        c.post(f"/internal/skill-drafts/{did}/complete", headers=SERVICE, json={"job_id": old_id, "token": claim["token"], "error": "bad"})
        assert c.post(f"/v1/skill-drafts/{did}/retry?expected_revision=0", headers=me).json()["status"] == "preparing"
        assert (
            c.post(f"/internal/skill-drafts/{did}/source", headers=SERVICE, json={"job_id": old_id, "token": claim["token"]}).status_code
            == 409
        )
        with SessionLocal() as db:
            old = db.get(OutboxMessage, old_id)
            old.claimed_until = None
            db.commit()
        assert c.post(f"/internal/jobs/{old_id}/claim", headers=SERVICE, json={"consumer": "old"}).json()["state"] == "done"
        assert c.get(f"/v1/skill-drafts/{did}", headers=me).json()["status"] == "preparing"
        assert c.delete(f"/v1/skill-drafts/{did}?expected_revision=1", headers=me).json()["status"] == "discarded"
        assert c.get(f"/v1/skill-drafts/{did}", headers=me).json()["files"] == []


def test_active_draft_limit():
    with TestClient(app) as c:
        me = _user(c)
        conv = _conversation(c, me)
        for _ in range(20):
            assert c.post(f"/v1/conversations/{conv}/skill-drafts", headers=me, json={}).status_code == 202
        assert c.post(f"/v1/conversations/{conv}/skill-drafts", headers=me, json={}).status_code == 409
        assert len(c.get("/v1/skill-drafts?active=1", headers=me).json()) == 20


def test_metadata_tracks_file_changes_without_list_storage_reads(monkeypatch):
    from ailab_platform.storage import get_storage

    with TestClient(app) as c:
        me = _user(c)
        name = "metadata-test"
        _upload(c, me, "skill.md", SKILL_MD.format(name=name).encode())
        draft = c.post(f"/v1/skills/{name}/drafts", headers=me, json={}).json()
        did = draft["id"]
        content = SKILL_MD.format(name="renamed-test")
        updated = c.put(f"/v1/skill-drafts/{did}/files/SKILL.md", headers=me, json={"content": content, "expected_revision": 0})
        assert updated.json()["name"] == "renamed-test"
        storage = get_storage()
        with monkeypatch.context() as m:
            m.setattr(storage, "get_bytes", lambda *args: (_ for _ in ()).throw(AssertionError("storage read")))
            assert c.get("/v1/skill-drafts", headers=me).json()[0]["name"] == "renamed-test"
        moved = c.post(f"/v1/skill-drafts/{did}/move", headers=me, json={"from": "SKILL.md", "to": "other.md", "expected_revision": 1})
        assert moved.json()["name"] is None
        moved = c.post(f"/v1/skill-drafts/{did}/move", headers=me, json={"from": "other.md", "to": "SKILL.md", "expected_revision": 2})
        assert moved.json()["name"] == "renamed-test"
        deleted = c.delete(f"/v1/skill-drafts/{did}/files/SKILL.md?expected_revision=3", headers=me)
        assert deleted.json()["name"] is None


def test_platform_draft_reused_without_notifications():
    with TestClient(app) as c:
        me = _user(c)
        first = c.post("/v1/skills/platform.pdf/drafts", headers=me, json={})
        assert first.status_code == 202
        second = c.post("/v1/skills/platform.pdf/drafts", headers=me, json={})
        assert second.status_code == 200 and second.json()["id"] == first.json()["id"]
        user_id = c.get("/v1/me", headers=me).json()["id"]
        with SessionLocal() as db:
            assert not db.scalar(select(Notification).where(Notification.user_id == user_id))
