"""Deletion boundaries: paused runs, durable history identities and safe job redelivery."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, Run
from tests.test_conversations import AUTH, SERVICE
from tests.test_document_context import upload


@pytest.mark.parametrize("state", ["waiting_user", "interrupted"])
def test_trash_cancels_suspended_source_consumers(state):
    with TestClient(app) as c:
        fid = upload(c, "suspended.txt", b"private", "text/plain")
        run = c.post("/v1/runs", headers=AUTH, json={"prompt": "read", "file_ids": [fid]}).json()
        with SessionLocal() as db:
            row = db.get(Run, run["id"])
            row.status = state
            row.runtime_meta = {"question": {"question_id": "q"}, "file_dependency": {"file_id": fid}}
            db.commit()
        assert c.delete(f"/v1/files/{fid}", headers=AUTH).status_code == 204
        with SessionLocal() as db:
            assert db.get(Run, run["id"]).status == "canceled"


def test_file_job_claim_after_purge_is_acknowledged_without_500():
    with TestClient(app) as c:
        file = c.post("/v1/files", headers=AUTH, files={"upload": ("pending.txt", b"private", "text/plain")}).json()
        from ailab_platform.models import OutboxMessage

        with SessionLocal() as db:
            job = db.scalar(select(OutboxMessage).where(OutboxMessage.payload["file_id"].as_string() == file["id"]))
            job_id = job.id
        c.delete(f"/v1/files/{file['id']}", headers=AUTH)
        c.delete("/v1/trash", headers=AUTH)
        response = c.post(f"/internal/jobs/{job_id}/claim", headers=SERVICE, json={"consumer": "late-worker"})
        assert response.status_code == 200 and response.json() == {"state": "done"}


def test_source_gc_serializes_with_user_writers(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager

    from ailab_platform import locks
    from ailab_platform.api.source_lifecycle import drain_invalidations
    from ailab_platform.models import FileSourceInvalidation

    waiting = threading.Event()
    real_lock = locks.user_critical_section

    @contextmanager
    def observed(db, uid):
        waiting.set()
        with real_lock(db, uid):
            yield

    with TestClient(app) as c:
        uid = c.get("/v1/me", headers=AUTH).json()["id"]
        with SessionLocal() as db:
            # Deterministic first pending identity; no file bytes are needed for this race.
            db.add(FileSourceInvalidation(file_id="000-lock-review", user_id=uid))
            db.commit()
        monkeypatch.setattr(locks, "user_critical_section", observed)

        def collect():
            with SessionLocal() as db:
                return drain_invalidations(db)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with SessionLocal() as db, real_lock(db, uid):
                future = pool.submit(collect)
                try:
                    assert waiting.wait(2), "source cleanup bypassed the user lock"
                    assert not future.done(), "cleanup raced a writer holding the user lock"
                finally:
                    db.rollback()
            future.result(timeout=5)


def test_library_read_rechecks_source_after_waiting_for_user_lock(monkeypatch):
    from contextlib import contextmanager

    from ailab_platform.api import routes_library_tools
    from ailab_platform.library import trash_lineages
    from ailab_platform.locks import user_critical_section

    with TestClient(app) as c:
        fid = upload(c, "race.txt", b"secret-read-race", "text/plain")
        run = c.post("/v1/runs", headers=AUTH, json={"prompt": "read", "file_ids": [fid]}).json()

        @contextmanager
        def delete_before_admission(db, uid):
            with SessionLocal() as writer, user_critical_section(writer, uid):
                trash_lineages(writer, uid, [writer.get(FileObject, fid).lineage_id])
                writer.commit()
            with user_critical_section(db, uid):
                yield

        monkeypatch.setattr(routes_library_tools, "user_critical_section", delete_before_admission)
        response = c.post(f"/internal/runs/{run['id']}/library/read", headers=SERVICE, json={"file_id": fid})
        assert response.status_code == 404
        assert "secret-read-race" not in response.text
