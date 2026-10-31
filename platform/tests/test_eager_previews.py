"""Readiness is independent of speculative previews; OCR reuse requires complete coverage."""

import hashlib

import pytest
from sqlalchemy import select

from ailab_platform.api.preview_requests import request_ready_preview
from ailab_platform.config import settings
from ailab_platform.contracts.file_preview import FilePreview, cached_page_preview
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, OutboxMessage
from ailab_platform.storage import get_storage
from ailab_platform.worker.preview import process_preview
from tests.test_file_previews import AUTH, ApiClient, upload
from tests.test_file_previews import api as api


def preview_jobs(db, fid):
    return list(
        db.scalars(
            select(OutboxMessage).where(
                OutboxMessage.payload["file_id"].as_string() == fid, OutboxMessage.payload["kind"].as_string() == "preview"
            )
        )
    )


def test_clean_scan_stages_preview_before_parse_and_exposes_only_scanned_file(api):
    fid = upload(api, "early.pdf", process=False)
    assert api.get(f"/v1/files/{fid}/preview", headers=AUTH).status_code == 404
    p = ApiClient(api)
    p.patch_file(fid, meta={"scan": {"verdict": "clean", "ok": True}})
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.status == "pending"
        assert len(preview_jobs(db, fid)) == 1
        job_id = file.meta["preview_job_id"]
    assert process_preview(fid, job_id, p)["status"] == "ready"
    assert api.get(f"/v1/files/{fid}/preview", headers=AUTH).json()["status"] == "ready"
    assert api.get(f"/v1/files/{fid}/preview/p-01.jpg", headers=AUTH).status_code == 200
    p.patch_file(fid, status="ready", meta={"processing": "done"})
    with SessionLocal() as db:
        assert len(preview_jobs(db, fid)) == 1
        assert db.get(FileObject, fid).meta["preview"]["status"] == "ready"


def test_worker_publishes_clean_scan_before_office_parse(api, monkeypatch):
    from ailab_platform.worker import office
    from ailab_platform.worker.isolate import IsolationError
    from ailab_platform.worker.pipeline import process_file

    fid = upload(api, "early.docx", process=False)

    def parse(file_id, platform, storage, output, scan):
        with SessionLocal() as db:
            file = db.get(FileObject, file_id)
            assert file.status == "pending"
            assert file.meta["scan"]["verdict"] == "clean"
            assert len(preview_jobs(db, file_id)) == 1
        raise IsolationError("test parser failure")

    monkeypatch.setattr(office, "read", parse)
    process_file(fid, ApiClient(api))


def test_requested_pages_are_staged_on_priority_queue(api, monkeypatch):
    from ailab_platform.api.preview_dispatch import dispatch_preview_jobs
    from ailab_platform.api.routes_ocr import request_page_render
    from ailab_platform.queue import get_queue, job_topic

    monkeypatch.setattr(settings, "preview_eager_max_bytes", 0)
    fid = upload(api, "priority.pdf")
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        request_page_render(db, file, [2])
        job = file.meta["preview_job_id"]
        db.commit()
    dispatch_preview_jobs(job)
    assert any(msg["job_id"] == job for msg in get_queue().outstanding(job_topic("preview", priority=True)))


def test_waiting_page_promotes_an_already_published_background_job(api):
    from ailab_platform.api.preview_dispatch import dispatch_preview_jobs
    from ailab_platform.api.routes_ocr import request_page_render
    from ailab_platform.queue import get_queue, job_topic

    fid = upload(api, "promote.pdf")
    with SessionLocal() as db:
        job = db.get(FileObject, fid).meta["preview_job_id"]
    dispatch_preview_jobs(job)
    assert any(msg["job_id"] == job for msg in get_queue().outstanding(job_topic("preview")))
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        request_page_render(db, file, [2])
        request_page_render(db, file, [2])
        db.commit()
        assert file.meta["preview_job_id"] == job
        assert len(preview_jobs(db, fid)) == 1
    dispatch_preview_jobs(job)
    assert any(msg["job_id"] == job for msg in get_queue().outstanding(job_topic("preview", priority=True)))


@pytest.mark.parametrize("name", ["a.docx", "a.pptx", "a.xlsx", "a.pdf", "a.xls"])
def test_readiness_stages_preview_without_a_click_and_without_rendering(api, name):
    fid = upload(api, name)
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.status == "ready"
        assert file.meta["preview"]["status"] == "pending"
        assert len(preview_jobs(db, fid)) == 1
        request_ready_preview(db, file)
        db.commit()
        assert len(preview_jobs(db, fid)) == 1
    p = ApiClient(api)
    if name.endswith((".pdf", ".docx", ".pptx", ".xls")):
        process_preview(fid, p.get_file(fid)["meta"]["preview_job_id"], p)
        assert api.get(f"/v1/files/{fid}/preview", headers=AUTH).json()["status"] == "ready"
        assert p.renders == 1


@pytest.mark.parametrize("condition", ["large", "disabled", "pending", "deleted", "purged", "text", "failed", "ready"])
def test_eager_admission_and_cached_terminal_states(api, monkeypatch, condition):
    monkeypatch.setattr(settings, "preview_eager_max_bytes", 0)
    fid = upload(api, "a.txt" if condition == "text" else "a.pdf", process=condition != "pending")
    monkeypatch.setattr(settings, "preview_eager_max_bytes", 0 if condition == "disabled" else 20)
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        if condition == "large":
            file.size = 21
        if condition in {"deleted", "purged"}:
            from ailab_platform.models import utcnow

            setattr(file, "deleted_at" if condition == "deleted" else "purged_at", utcnow())
        if condition in {"failed", "ready"}:
            file.meta = {**file.meta, "preview": {"status": condition, "kind": "pages", "reason": "render_failed"}}
        request_ready_preview(db, file)
        db.commit()
        assert preview_jobs(db, fid) == []


def test_office_artifact_delivery_stages_one_preview_in_delivery_transaction(api):
    from tests.run_claims import attempt_headers, delivery_headers

    run = api.post("/v1/runs", headers=AUTH, json={"prompt": "office artifact"}).json()
    headers = attempt_headers(api, run["id"])
    assert api.post(f"/internal/runs/{run['id']}/status", headers=headers, json={"status": "running"}).status_code == 200
    key = f"staging/{run['id']}/output.docx"
    get_storage().put_bytes(key, b"artifact")
    body = {
        "run_id": run["id"],
        "staging_key": key,
        "filename": "output.docx",
        "size": 8,
        "sha256": hashlib.sha256(b"artifact").hexdigest(),
    }
    headers = delivery_headers(headers, run["id"], key)
    first = api.post("/internal/artifacts", headers=headers, json=body)
    assert first.status_code == 200, first.text
    assert api.post("/internal/artifacts", headers=headers, json=body).json() == first.json()
    with SessionLocal() as db:
        fid = first.json()["file_id"]
        assert db.get(FileObject, fid).meta["preview"]["status"] == "pending"
        assert len(preview_jobs(db, fid)) == 1


def page_cache(fid, count=2, pages=None):
    names = pages or ["p-01.jpg", "p-02.jpg"]
    return {
        "ocr_preview": {"status": "ready", "kind": "pages", "pages": names, "page_count": count},
        "ocr_preview_assets": {
            n: {"key": f"derived/{fid}/preview/{n}", "content_type": "image/jpeg", "sha256": hashlib.sha256(b"jpeg").hexdigest()}
            for n in names
        },
    }


def test_complete_ocr_pages_reused_without_a_sandbox_and_old_manifest_still_valid(api):
    fid = upload(api, "reuse.pdf")
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.meta = {**file.meta, **page_cache(fid)}
        job_id = file.meta["preview_job_id"]
        db.commit()
    for name in ("p-01.jpg", "p-02.jpg"):
        get_storage().put_bytes(f"derived/{fid}/preview/{name}", b"jpeg", "image/jpeg")
    p = ApiClient(api)
    assert process_preview(fid, job_id, p)["status"] == "ready"
    assert p.renders == 0
    result = api.get(f"/v1/files/{fid}/preview", headers=AUTH).json()
    assert FilePreview.model_validate(result).pages == ["p-01.jpg", "p-02.jpg"]
    asset = api.get(f"/v1/files/{fid}/preview/p-01.jpg", headers=AUTH)
    assert "immutable" in asset.headers["cache-control"]
    assert api.get(f"/v1/files/{fid}/preview/p-01.jpg", headers={**AUTH, "If-None-Match": asset.headers["etag"]}).status_code == 304


@pytest.mark.parametrize("tamper", ["partial", "unknown_count", "foreign", "digest", "mime"])
def test_partial_or_invalid_cache_does_not_claim_complete_preview(tamper):
    meta = page_cache("f")
    if tamper == "partial":
        meta["ocr_preview"]["page_count"] = 3
    elif tamper == "unknown_count":
        meta["ocr_preview"].pop("page_count")
    elif tamper == "foreign":
        meta["ocr_preview_assets"]["p-01.jpg"]["key"] = "derived/another/preview/p-01.jpg"
    elif tamper == "digest":
        meta["ocr_preview_assets"]["p-01.jpg"]["sha256"] = "bad"
    else:
        meta["ocr_preview_assets"]["p-01.jpg"]["content_type"] = "application/json"
    assert cached_page_preview("f", meta) is None
