"""Safety READY and consumption-time native jobs use real API/storage/subprocess contracts."""

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import DerivedAsset, DerivedBudget, FileObject, OutboxMessage
from ailab_platform.worker.native_preparation import process_native
from ailab_platform.worker.pipeline import process_file


@pytest.mark.parametrize("oversized", [False, True])
def test_safe_original_is_ready_before_lazy_native_and_failure_does_not_break_work(oversized):
    payload = ("x" * 200001 if oversized else "按需读取正文，不在上传期间提取。\n" * 20).encode()
    headers = {"X-Service-Token": "test-service"}
    with TestClient(app) as client:

        class Platform:
            attempt = None

            def request(self, method, url, body=None):
                response = client.request(
                    method, url, json=body, headers={**headers, **({"X-Run-Attempt": self.attempt} if self.attempt else {})}
                )
                assert response.status_code < 400, response.text
                return response.json()

            def get_file(self, fid, job="processing"):
                return self.request("GET", f"/internal/files/{fid}?job={job}")

            def download_file(self, fid, target, **kwargs):
                response = client.get(f"/internal/files/{fid}/content?job={kwargs.get('job', 'processing')}", headers=headers)
                assert response.status_code == 200, response.text
                target.write_bytes(response.content)

            def patch_file(self, fid, **kwargs):
                return self.request("PATCH", f"/internal/files/{fid}", kwargs)

            def prewarm_render(self):
                raise AssertionError("safe uploads must not touch the system pool")

            def start_native(self, fid, jid):
                return self.request("POST", f"/internal/files/{fid}/native/start", {"job_id": jid})

            def complete_native(self, fid, jid, **kwargs):
                return self.request("POST", f"/internal/files/{fid}/native/complete", {"job_id": jid, **kwargs})

            def read_document(self, *args):
                raise AssertionError("plain text must not use an Office sandbox")

            def reserve_derived_batch(self, items):
                return self.request("POST", "/internal/derived-assets/reserve-batch", {"items": items})

            def finish_derived_batch(self, items):
                return self.request("POST", "/internal/derived-assets/finish-batch", {"items": items})

            def abandon_derived_batch(self, items):
                return self.request("POST", "/internal/derived-assets/abandon-batch", {"items": items})

            def discard_derived_batch(self, items):
                return self.request("POST", "/internal/derived-assets/discard-batch", {"items": items})

        auth = {"Authorization": "Bearer test-token"}
        name = f"lazy-input-{'over' if oversized else 'small'}.txt"
        upload = client.post("/v1/files?disposition=copy", headers=auth, files={"upload": (name, payload, "text/plain")})
        assert upload.status_code == 201, upload.text
        fid = upload.json()["id"]
        platform = Platform()
        with SessionLocal() as db:
            job = db.scalar(select(OutboxMessage).where(OutboxMessage.payload["file_id"].as_string() == fid))
            jid = job.id
        claim = platform.request("POST", f"/internal/jobs/{jid}/claim", {"consumer": "lazy-input-test"})
        platform.attempt = claim["token"]
        assert process_file(fid, platform)["safety_ready"]
        platform.request("POST", f"/internal/jobs/{jid}/ack", {"token": platform.attempt})
        platform.attempt = None
        safe = client.get(f"/v1/files/{fid}", headers=auth).json()
        assert safe["status"] == "ready" and safe["sha256"] == hashlib.sha256(payload).hexdigest()
        assert "text_key" not in safe["meta"]
        assert all(v["status"] == "absent" for v in safe["meta"]["representations"].values())
        with SessionLocal() as db:
            assert db.scalar(select(DerivedAsset).where(DerivedAsset.file_id == fid)) is None
            assert db.scalar(select(OutboxMessage).where(OutboxMessage.payload["file_id"].as_string() == fid)) is None
            from ailab_platform.api.derived_assets import backfill_batch
            from ailab_platform.storage import get_storage

            for _ in range(30):
                backfill_batch(db, get_storage())
                db.commit()
                if db.get(DerivedBudget, 1, populate_existing=True).backfill_complete:
                    break
            assert db.get(DerivedBudget, 1).backfill_complete
        pending = client.post("/v1/runs", headers=auth, json={"mode": "chat", "prompt": "读取附件", "file_ids": [fid]})
        assert pending.status_code == 409, pending.text
        with SessionLocal() as db:
            state = db.get(FileObject, fid).meta["representations"]["native_text"]
            assert state["status"] == "pending"
            jid = state["job_id"]
        platform.attempt = platform.request("POST", f"/internal/jobs/{jid}/claim", {"consumer": "lazy-native-test"})["token"]
        process_native(platform, fid, jid)
        platform.request("POST", f"/internal/jobs/{jid}/ack", {"token": platform.attempt})
        platform.attempt = None
        prepared = client.get(f"/v1/files/{fid}", headers=auth).json()
        assert prepared["status"] == "ready"
        assert prepared["meta"]["representations"]["native_text"]["status"] == ("failed" if oversized else "ready")
        chat = client.post("/v1/runs", headers=auth, json={"mode": "chat", "prompt": "读取附件", "file_ids": [fid]})
        assert chat.status_code == (422 if oversized else 201), chat.text
        work = client.post("/v1/runs", headers=auth, json={"mode": "work", "prompt": "处理原文件", "file_ids": [fid]})
        assert work.status_code == 201, work.text
        with SessionLocal() as db:
            assets = db.scalars(select(DerivedAsset).where(DerivedAsset.file_id == fid)).all()
            assert len(assets) == (0 if oversized else 2)
            assert all(a.state == "ready" for a in assets)
            assert db.get(DerivedBudget, 1).reserved_bytes == 0


def test_detected_image_type_is_normalized_and_work_prepares_pixels_only_when_sent(monkeypatch):
    import io

    from PIL import Image

    from ailab_platform.catalog import ModelSpec, get_catalog
    from ailab_platform.worker.preview import process_preview
    from tests.test_file_previews import ApiClient

    monkeypatch.setitem(
        get_catalog().models,
        "safe-image-vision",
        ModelSpec(id="safe-image-vision", name="Vision", provider="mock", capabilities=["tools", "vision"]),
    )
    image = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(image, "PNG")
    auth = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        uploaded = client.post(
            "/v1/files?disposition=copy", headers=auth, files={"upload": ("typed-input.data", image.getvalue(), "application/octet-stream")}
        ).json()
        fid = uploaded["id"]
        process_file(fid, ApiClient(client))
        safe = client.get(f"/v1/files/{fid}", headers=auth).json()
        assert safe["status"] == "ready" and safe["content_type"] == "image/png"
        assert "display_key" not in safe["meta"]
        body = {"mode": "work", "model": "safe-image-vision", "prompt": "处理图片", "file_ids": [fid]}
        preparing = client.post("/v1/runs", headers=auth, json=body)
        assert preparing.status_code == 409, preparing.text
        with SessionLocal() as db:
            meta = db.get(FileObject, fid).meta
            assert "native_text" in meta["representations"] and meta["representations"]["native_text"]["status"] == "absent"
            assert (
                db.scalar(
                    select(OutboxMessage).where(
                        OutboxMessage.payload["file_id"].as_string() == fid, OutboxMessage.payload["kind"].as_string() == "ocr"
                    )
                )
                is None
            )
            job_id = meta["preview_job_id"]
        process_preview(fid, job_id, ApiClient(client))
        ready = client.post("/v1/runs", headers=auth, json=body)
        assert ready.status_code == 201, ready.text
