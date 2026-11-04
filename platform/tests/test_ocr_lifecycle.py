"""Selected-page identity, durable ownership and terminal rendering failures."""

from datetime import UTC, datetime, timedelta

from ailab_platform.clients import ClientError
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, OutboxMessage
from ailab_platform.queue import InMemoryQueue
from ailab_platform.storage import get_storage
from ailab_platform.worker import main
from tests.test_ocr import AUTH, SERVICE, OCRClient, api, blank_pdf, photo, queued_ocr, rapid_upstream, run_ocr_job  # noqa: F401


def ready_pdf(api):  # noqa: F811
    response = api.post(
        "/v1/files", headers=AUTH, params={"disposition": "copy"}, files={"upload": ("selected.pdf", blank_pdf(2), "application/pdf")}
    )
    assert response.status_code == 201
    fid = response.json()["id"]
    # These cases exercise API/queue identity, not scanning or Broker rendering.
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.status = "ready"
        file.meta = {"scan": {"verdict": "clean"}, "extract": {"units": 2}}
        db.commit()
    return fid


def request(api, fid, pages):  # noqa: F811
    return api.post(f"/v1/files/{fid}/extract-text", headers=AUTH, json={"pages": pages})


def rendered(fid, page):
    key = f"derived/{fid}/preview/p-{page}.jpg"
    get_storage().put_bytes(key, photo(), "image/jpeg")
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.meta = {
            **file.meta,
            "ocr_preview": {"status": "ready", "kind": "pages", "pages": [f"p-{page}.jpg"]},
            "ocr_preview_assets": {f"p-{page}.jpg": {"key": key}},
        }
        db.commit()


def test_mixed_and_fully_cached_selections_download_exact_pages(api, rapid_upstream):  # noqa: F811
    fid = ready_pdf(api)
    first = request(api, fid, [1]).json()
    rendered(fid, 1)
    assert run_ocr_job(api, fid)["ocr"] == "done"
    second = request(api, fid, [2, 1]).json()
    rendered(fid, 2)
    assert run_ocr_job(api, fid)["ocr"] == "done"
    status = api.get(f"/v1/files/{fid}/extract-text", headers=AUTH, params={"request_id": second["request_id"]}).json()
    text = api.get(status["download_url"], headers=AUTH).text
    assert status["status"] == "done" and status["pages"] == [1, 2]
    assert text.index("[page:1]") < text.index("[page:2]")
    assert len(rapid_upstream[0]) == 2
    third = request(api, fid, [1]).json()
    assert third["ocr"] == "done"
    text = api.get(f"/v1/files/{fid}/extracted-text", headers=AUTH, params={"request_id": third["request_id"]}).text
    assert "[page:1]" in text and "[page:2]" not in text
    assert len(rapid_upstream[0]) == 2
    for endpoint in ("extract-text", "extracted-text"):
        for old in (first, second):
            assert api.get(f"/v1/files/{fid}/{endpoint}", headers=AUTH, params={"request_id": old["request_id"]}).status_code == 409


def test_active_selection_is_idempotent_and_conflicts_are_explicit(api, rapid_upstream):  # noqa: F811
    fid = ready_pdf(api)
    first = request(api, fid, [1]).json()
    assert request(api, fid, [1]).json()["request_id"] == first["request_id"]
    assert request(api, fid, [2]).status_code == 409
    assert request(api, fid, [1, 2]).status_code == 409
    assert len(queued_ocr(fid)) == 1
    rendered(fid, 1)
    assert run_ocr_job(api, fid)["ocr"] == "done"


def test_old_claim_and_expiry_cannot_fail_new_generation(api, rapid_upstream):  # noqa: F811
    fid = ready_pdf(api)
    request(api, fid, [1])
    old = queued_ocr(fid)[0]
    token = api.post(f"/internal/jobs/{old['job_id']}/claim", headers=SERVICE, json={"consumer": "old"}).json()["token"]
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.meta = {**file.meta, "ocr": "failed", "ocr_reason": "ocr_failed"}
        db.commit()
    new = request(api, fid, [2]).json()
    for suffix in ("/prepare", ""):
        response = api.post(f"/internal/files/{fid}/ocr{suffix}", headers={**SERVICE, "X-Run-Attempt": token}, json={})
        assert response.status_code == 409
    with SessionLocal() as db:
        job = db.get(OutboxMessage, old["job_id"])
        job.claimed_until = datetime.now(UTC) - timedelta(seconds=1)
        job.payload = {**job.payload, "deadline": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
        db.commit()
    assert api.post(f"/internal/jobs/{old['job_id']}/claim", headers=SERVICE, json={"consumer": "late"}).json() == {"state": "done"}
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.meta["ocr"] == "pending" and file.meta["ocr_generation"] == new["request_id"]
        assert file.meta["ocr_pdf"]["low_text_pages"] == [1]
        assert db.get(OutboxMessage, old["job_id"]) is None


def drive(api, monkeypatch, fid, *, busy=False):  # noqa: F811
    renders = []

    class Renderer(OCRClient):
        def render_preview(self, file_id, job_id):
            renders.append(job_id)
            if busy:
                raise ClientError("POST", "/render", 503, "capacity")
            return {"preview": {"status": "failed", "kind": "pages", "reason": "unsupported_pdf"}, "assets": {}}

    monkeypatch.setattr(main, "PlatformClient", lambda **kwargs: Renderer(api))
    payload = queued_ocr(fid)[0]
    queue = InMemoryQueue()
    for _ in range(2):
        queue.enqueue(settings.file_queue, payload)
        main.handle_message(queue, queue.dequeue(settings.file_queue, timeout=0))
    return renders, payload["job_id"]


def test_terminal_renderer_failure_finishes_once_and_allows_explicit_retry(api, rapid_upstream, monkeypatch):  # noqa: F811
    fid = ready_pdf(api)
    first = request(api, fid, [1]).json()
    renders, jid = drive(api, monkeypatch, fid)
    assert len(renders) == 1
    with SessionLocal() as db:
        assert db.get(FileObject, fid).meta["ocr"] == "failed"
        assert db.get(FileObject, fid).meta["ocr_reason"] == "preview_unavailable"
        assert db.get(OutboxMessage, jid) is None
    second = request(api, fid, [1]).json()
    assert second["request_id"] != first["request_id"]
    retried, _ = drive(api, monkeypatch, fid)
    assert len(retried) == 1 and retried[0] != renders[0]
    assert not rapid_upstream[0]


def test_busy_renderer_retries_same_render_identity(api, rapid_upstream, monkeypatch):  # noqa: F811
    fid = ready_pdf(api)
    request(api, fid, [1])
    renders, jid = drive(api, monkeypatch, fid, busy=True)
    assert len(renders) == 2 and renders[0] == renders[1]
    with SessionLocal() as db:
        assert db.get(FileObject, fid).meta["ocr"] == "pending"
        assert db.get(OutboxMessage, jid).claim_token is None


def test_missing_render_asset_converges_to_failed(api, rapid_upstream):  # noqa: F811
    fid = ready_pdf(api)
    request(api, fid, [1])
    response = api.post(f"/internal/files/{fid}/ocr", headers=SERVICE, json={})
    assert response.status_code == 200 and response.json()["ocr"] == "failed"
    with SessionLocal() as db:
        assert db.get(FileObject, fid).meta["ocr_reason"] == "preview_unavailable"


def test_late_inference_cannot_publish_or_fail_a_new_request(api, rapid_upstream, monkeypatch):  # noqa: F811
    from ailab_platform import ocr_backend

    fid = ready_pdf(api)
    request(api, fid, [1])
    rendered(fid, 1)
    old = queued_ocr(fid)[0]
    token = api.post(f"/internal/jobs/{old['job_id']}/claim", headers=SERVICE, json={"consumer": "old"}).json()["token"]
    changed = []

    async def late_result(data):
        with SessionLocal() as db:
            file = db.get(FileObject, fid)
            file.meta = {**file.meta, "ocr": "failed"}
            db.commit()
        changed.append(request(api, fid, [2]).json()["request_id"])
        return {"text": "late text", "engine": "rapidocr:test", "backend": "cann", "seconds": 0.1}

    monkeypatch.setattr(ocr_backend, "recognize", late_result)
    response = api.post(f"/internal/files/{fid}/ocr", headers={**SERVICE, "X-Run-Attempt": token}, json={})
    assert response.status_code == 409
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.meta["ocr"] == "pending" and file.meta["ocr_generation"] == changed[0]
        assert not file.meta.get("ocr_text_key") and not file.meta.get("ocr_pages")


def test_legacy_job_without_current_identity_can_finish(api, rapid_upstream):  # noqa: F811
    fid = ready_pdf(api)
    request(api, fid, [1])
    rendered(fid, 1)
    job = queued_ocr(fid)[0]
    token = api.post(f"/internal/jobs/{job['job_id']}/claim", headers=SERVICE, json={"consumer": "legacy"}).json()["token"]
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        meta = dict(file.meta)
        meta.pop("ocr_job_id")
        meta.pop("ocr_selected_pages")
        file.meta = meta
        db.commit()
    response = api.post(f"/internal/files/{fid}/ocr", headers={**SERVICE, "X-Run-Attempt": token}, json={})
    assert response.status_code == 200 and response.json()["ocr"] == "done"
