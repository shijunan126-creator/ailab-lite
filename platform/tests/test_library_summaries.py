"""Summary settings, transactional staging, OCR late text and bounded tool results."""

from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

from ailab_platform.api.main import app
from ailab_platform.api.routes_library_summary import request_summary
from ailab_platform.catalog import get_catalog
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, OutboxMessage, UserRole
from ailab_platform.storage import get_storage
from ailab_platform.worker.pipeline import process_file
from tests.faults.test_library_summary import Model, deliver, summary_file
from tests.test_conversations import AUTH, _other_user
from tests.test_file_previews import SERVICE, ApiClient
from tests.test_ocr import upstream as upstream


def test_library_settings_platform_admin_and_disabled_pending_job(monkeypatch):
    with TestClient(app) as c:
        other = _other_user(c)
        assert c.get("/v1/admin/settings/library", headers=other).status_code == 403
        assert c.patch("/v1/admin/settings/library", headers=other, json={"summaries": False}).status_code == 403
        uid = c.get("/v1/me", headers=other).json()["id"]
        with SessionLocal() as db:
            db.add(UserRole(user_id=uid, role="platform_admin"))
            db.commit()
        assert c.get("/v1/admin/settings/library", headers=other).json() == {"summaries": True}
        fid, job = summary_file(c)
        assert c.patch("/v1/admin/settings/library", headers=other, json={"summaries": False}).json() == {"summaries": False}
        try:
            model = Model()
            monkeypatch.setattr("ailab_platform.api.routes_library_summary.llm_for", lambda alias: model)
            queue, _ = deliver(c, job)
            assert queue.acks == 1 and not model.calls
            with SessionLocal() as db:
                assert db.get(FileObject, fid).meta["summary"]["reason"] == "disabled"
                f = FileObject(user_id=uid, filename=f"disabled-{uuid4().hex}.txt", storage_key="unused", status="ready")
                db.add(f)
                db.flush()
                request_summary(db, f)
                assert not (f.meta or {}).get("summary")
                db.rollback()
        finally:
            c.patch("/v1/admin/settings/library", headers=other, json={"summaries": True}).raise_for_status()


def test_ready_stages_once_without_inference_and_transaction_rolls_back(monkeypatch):
    with TestClient(app) as c:
        body = ("新的资料文本。" * 100).encode()
        response = c.post("/v1/files", headers=AUTH, files={"upload": (f"ready-{uuid4().hex}.txt", body, "text/plain")})
        assert response.status_code == 201
        fid = response.json()["id"]
        model = Model()
        monkeypatch.setattr("ailab_platform.api.routes_library_summary.llm_for", lambda alias: model)
        process_file(fid, platform=ApiClient(c))
        with SessionLocal() as db:
            f = db.get(FileObject, fid)
            assert f.status == "ready" and f.meta["summary"]["status"] == "pending"
            job = f.meta["summary"]["job_id"]
            assert db.get(OutboxMessage, job).payload["kind"] == "summary"
            request_summary(db, f)
            db.commit()
            assert f.meta["summary"]["job_id"] == job
            rollback_file = FileObject(
                user_id=f.user_id,
                filename=f"rollback-{uuid4().hex}.txt",
                storage_key="unused",
                status="ready",
                meta={"excerpt": "rollback" * 100},
            )
            db.add(rollback_file)
            db.flush()
            request_summary(db, rollback_file)
            rollback_job = rollback_file.meta["summary"]["job_id"]
            db.rollback()
            assert db.get(OutboxMessage, rollback_job) is None
        assert not model.calls
        queue, _ = deliver(c, job)
        assert queue.acks == 1 and len(model.calls) == 1


def test_short_text_skips_and_late_ocr_stages_exactly_once(monkeypatch):
    with TestClient(app) as c:
        fid, job = summary_file(c, text="short")
        model = Model()
        monkeypatch.setattr("ailab_platform.api.routes_library_summary.llm_for", lambda alias: model)
        deliver(c, job)
        assert not model.calls
        with SessionLocal() as db:
            f = db.get(FileObject, fid)
            assert f.meta["summary"]["status"] == "skipped"
            key = f"derived/{fid}/ocr.txt"
            get_storage().put_bytes(key, ("\f[page:1]\n" + "扫描内容。" * 100).encode())
            f.meta = {**f.meta, "ocr": "done", "ocr_text_key": key}
            request_summary(db, f)
            db.commit()
            new_job = f.meta["summary"]["job_id"]
            assert new_job != job and f.meta["summary"]["source"] == "ocr"
            request_summary(db, f)
            db.commit()
            assert f.meta["summary"]["job_id"] == new_job
        deliver(c, new_job)
        assert len(model.calls) == 1 and model.calls[0][0].call_id == f"summary:{fid}"
        with SessionLocal() as db:
            f = db.get(FileObject, fid)
            request_summary(db, f)
            db.commit()
            assert f.meta["summary"]["status"] == "done"


def test_summary_real_transport_disables_declared_reasoning_and_search_includes_text(upstream):
    seen, reply = upstream
    spec_id = next(k for k in get_catalog().models if k.startswith("vision_"))
    original = get_catalog().models[spec_id]
    get_catalog().models[spec_id] = replace(original, disable_reasoning_for_quiet=True)
    reply["text"] = "资料摘要"
    try:
        with TestClient(app) as c:
            fid, job = summary_file(c)
            deliver(c, job)
            assert len(seen) == 1
            payload = seen[0][1]
            assert payload["thinking"] == {"type": "disabled"}
            run = c.post("/v1/runs", headers=AUTH, json={"prompt": "search", "mode": "chat"}).json()
            result = c.post(f"/internal/runs/{run['id']}/library/search", headers=SERVICE, json={"query": "summary-", "limit": 20}).json()
            assert next(f for f in result["files"] if f["file_id"] == fid)["summary"] == "资料摘要"
    finally:
        get_catalog().models[spec_id] = original
