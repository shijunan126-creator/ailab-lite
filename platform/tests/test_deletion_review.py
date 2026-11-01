"""Deletion boundaries: paused runs, durable history identities and safe job redelivery."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.api.reaper import purge_deleted_conversations
from ailab_platform.db import SessionLocal
from ailab_platform.models import Artifact, Conversation, FileObject, Run, utcnow
from ailab_platform.storage import get_storage
from tests.test_conversations import AUTH, _finish
from tests.test_document_context import upload


@pytest.mark.parametrize("action", ["purge", "empty", "retention"])
def test_deleted_files_keep_explicit_history_placeholders(action):
    from ailab_platform.api.reaper import purge_expired_trash

    with TestClient(app) as c:
        fid = upload(c, "history.txt", b"private", "text/plain")
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "read", "file_ids": [fid]}).json()
        _finish(c, run["id"], "secret-summary", thinking={"text": "secret-thinking"})
        get_storage().put_bytes(f"previews/{run['id']}/secret.txt", b"secret-preview")
        with SessionLocal() as db:
            artifact = Artifact(run_id=run["id"], file_id=fid)
            db.add(artifact)
            db.commit()
            aid = artifact.id
        assert c.delete(f"/v1/files/{fid}", headers=AUTH).status_code == 204

        def check(name):
            response = c.get(f"/v1/conversations/{conv['id']}", headers=AUTH)
            assert response.status_code == 200, response.text
            turn = response.json()["turns"][0]
            assert turn["input_files"] == [{"file_id": fid, "filename": name, "deleted": True}]
            artifact = turn["artifacts"][0]
            assert artifact["id"] == aid and artifact["file_id"] == fid and artifact["deleted"]
            assert artifact["download_url"] == "" and artifact["filename"] == name

        check("history.txt")  # in the trash the owner still sees which file it was
        if action == "retention":
            with SessionLocal() as db:
                db.get(FileObject, fid).purge_at = utcnow() - timedelta(days=1)
                db.commit()
                assert purge_expired_trash(db) >= 1
        elif action == "empty":
            assert c.delete("/v1/trash", headers=AUTH).status_code == 200
        else:
            result = c.post("/v1/files/batch", headers=AUTH, json={"ids": [fid], "action": "purge"})
            assert result.status_code == 200, result.text
        check("文件已删除")
        assert c.get(f"/v1/files/{fid}/content", headers=AUTH).status_code == 404
        fork = c.post(f"/v1/conversations/{conv['id']}/fork", headers=AUTH, json={"run_id": run["id"], "mode": "work"})
        assert fork.status_code == 201, fork.text
        copied = c.get(f"/v1/conversations/{fork.json()['id']}", headers=AUTH).json()["turns"][0]
        assert copied["artifacts"][0]["deleted"] and copied["input_files"][0]["deleted"]
        with SessionLocal() as db:
            row = db.get(Run, copied["id"])
            assert row.summary == "（文件来源已失效）"
            assert "secret-thinking" not in str(row.result)
            from ailab_platform.models import RunEvent

            assert "secret-" not in str([e.payload for e in db.scalars(select(RunEvent).where(RunEvent.run_id == row.id))])
        assert not get_storage().list_prefix(f"previews/{copied['id']}/").items


@pytest.mark.parametrize("project", [False, True])
@pytest.mark.parametrize("state", ["running", "waiting_user", "interrupted"])
def test_conversation_delete_and_retention_keep_library_files(project, state, monkeypatch):
    from ailab_platform.config import settings

    monkeypatch.setattr(settings, "gc_batch_size", 100)
    with TestClient(app) as c:
        pid = c.post("/v1/projects", headers=AUTH, json={"name": f"retained-{state}"}).json()["id"] if project else None
        fid = upload(c, "retained.txt", b"retained", "text/plain")
        conv = c.post("/v1/conversations", headers=AUTH, json={"project_id": pid}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "read", "file_ids": [fid]}).json()
        with SessionLocal() as db:
            row = db.get(Run, run["id"])
            row.status = state
            artifact = FileObject(
                user_id=row.user_id,
                project_id=pid,
                filename=f"delivered-{run['id']}.txt",
                status="ready",
                storage_key=f"runs/{run['id']}/output",
            )
            db.add(artifact)
            db.flush()
            get_storage().put_bytes(artifact.storage_key, b"delivered")
            db.add(Artifact(run_id=row.id, file_id=artifact.id))
            db.commit()
            delivered = artifact.id
        assert c.delete(f"/v1/conversations/{conv['id']}", headers=AUTH).status_code == 204
        with SessionLocal() as db:
            assert db.get(Run, run["id"]).status == "canceled"
            db.get(Conversation, conv["id"]).deleted_at = utcnow() - timedelta(days=100)
            db.commit()
            for _ in range(20):
                purge_deleted_conversations(db, utcnow())
                if db.get(Conversation, conv["id"]) is None:
                    break
            assert db.get(Conversation, conv["id"]) is None
        assert c.get(f"/v1/files/{fid}/content", headers=AUTH).content == b"retained"
        assert c.get(f"/v1/files/{delivered}/content", headers=AUTH).content == b"delivered"
