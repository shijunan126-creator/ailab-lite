"""Public library preview contract, with the real API/Worker isolation boundary."""

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ailab_platform.api.main import app
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject
from ailab_platform.queue import get_queue, job_topic
from ailab_platform.storage import get_storage
from ailab_platform.worker.pipeline import process_file
from ailab_platform.worker.preview import process_preview

AUTH = {"Authorization": "Bearer test-token"}
SERVICE = {"X-Service-Token": "test-service"}


class ApiClient:
    def __init__(self, api):
        self.api = api
        self.renders = 0

    def get_file(self, fid, job="processing", **binding):
        response = self.api.get(f"/internal/files/{fid}", headers=SERVICE, params={"job": job, **binding})
        response.raise_for_status()
        return response.json()

    def download_file(self, fid, target, job="processing", **kwargs):
        response = self.api.get(
            f"/internal/files/{fid}/content", headers=SERVICE, params={"job": job, **{k: v for k, v in kwargs.items() if k != "max_bytes"}}
        )
        response.raise_for_status()
        assert len(response.content) <= kwargs["max_bytes"]
        target.write_bytes(response.content)

    def patch_file(self, fid, job="processing", **kwargs):
        response = self.api.patch(f"/internal/files/{fid}", headers=SERVICE, json=kwargs, params={"job": job})
        response.raise_for_status()
        return response.json()

    def complete_preview(self, fid, job_id, preview, assets):
        response = self.api.post(
            f"/internal/files/{fid}/preview", headers=SERVICE, json={"job_id": job_id, "preview": preview, "assets": assets}
        )
        response.raise_for_status()

    def prepare_document(self, fid, scan):
        response = self.api.post(f"/internal/files/{fid}/prepare-document", headers=SERVICE, json={"scan": scan})
        response.raise_for_status()
        return response.json()

    def read_document(self, fid, job_id):
        from ailab_platform.adapters.lease_store_memory import MemoryLeaseStore
        from ailab_platform.broker.documents import DocumentReader
        from ailab_platform.broker.service import LeaseManager
        from ailab_platform.sandbox.mock import MockSandbox
        from tests.fakes import FakeRedis

        class TestProvider(MockSandbox):
            def exec(self, ref, command, timeout):
                if "warmup.fodt" in command or "render_sandbox_health.py" in command:
                    from types import SimpleNamespace

                    return SimpleNamespace(exit_code=0, stdout='{"tmp_used":0}')
                return super().exec(ref, command, timeout)

        class BoundPlatform:
            def get_file(self, file_id, **binding):
                result = self_api.get_file(file_id, **binding)
                with SessionLocal() as db:
                    result["render_owner_id"] = db.get(FileObject, file_id).user_id
                return result

            def download_file(self, *args, **kwargs):
                return self_api.download_file(*args, **kwargs)

        self_api = self
        manager = LeaseManager(
            provider=TestProvider(),
            platform=BoundPlatform(),
            storage=get_storage(),
            redis_client=FakeRedis(),
            store=MemoryLeaseStore(),
            start_background=False,
        )
        try:
            return DocumentReader(manager).read(fid, job_id)
        finally:
            manager.close()

    def render_preview(self, fid, job_id):
        self.renders += 1
        if self.get_file(fid, job="preview")["filename"].endswith((".xls", ".ods")):
            key = f"derived/{fid}/preview/sheets.json"
            get_storage().put_bytes(key, b'{"sheets": [], "errors": []}', "application/json")
            return {
                "preview": {"status": "ready", "kind": "sheets", "data": "sheets.json"},
                "assets": {
                    "sheets.json": {
                        "key": key,
                        "sha256": hashlib.sha256(b'{"sheets": [], "errors": []}').hexdigest(),
                        "content_type": "application/json",
                    }
                },
            }
        key = f"derived/{fid}/preview/p-01.jpg"
        get_storage().put_bytes(key, b"jpeg", "image/jpeg")
        return {
            "preview": {"status": "ready", "kind": "pages", "pages": ["p-01.jpg"], "page_count": 57},
            "assets": {"p-01.jpg": {"key": key, "sha256": hashlib.sha256(b"jpeg").hexdigest(), "content_type": "image/jpeg"}},
        }


@pytest.fixture
def api(monkeypatch):
    # These tests inspect the durable preview before its worker claims it.
    monkeypatch.setattr(settings, "render_prewarm", 0)
    with TestClient(app) as client:
        yield client


def upload(api, name, data=b"hello", mime="application/octet-stream", *, process=True):
    r = api.post("/v1/files", headers=AUTH, files={"upload": (name, data, mime)})
    assert r.status_code == 201
    fid = r.json()["id"]
    if process:
        process_file(fid, platform=ApiClient(api))
        # Simulate the asynchronous dispatcher; readiness now stages office/PDF jobs eagerly.
        from ailab_platform.api.preview_dispatch import dispatch_preview_jobs

        dispatch_preview_jobs()
    return fid


def preview(api, fid):
    response = api.get(f"/v1/files/{fid}/preview", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "name,fmt,language",
    [
        ("test.md", "markdown", None),
        ("test.py", "code", "python"),
        ("test.txt", "plain", None),
        ("test.yaml", "code", "yaml"),
        ("test.json", "code", "json"),
        ("test.log", "plain", None),
    ],
)
def test_text_preview_no_job_and_plain_asset(api, name, fmt, language):
    fid = upload(api, name, b"<script>alert(1)</script>")
    assert preview(api, fid) == {
        "status": "ready",
        "kind": "text",
        "text": "text.txt",
        "format": fmt,
        "language": language,
        "truncated": False,
    }
    assert not any(p.get("preview_only") and p["file_id"] == fid for p in get_queue().outstanding(settings.file_queue))
    response = api.get(f"/v1/files/{fid}/preview/text.txt", headers=AUTH)
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"].endswith("sandbox")
    assert response.content == b"<script>alert(1)</script>"
    cached = api.get(f"/v1/files/{fid}/preview/text.txt", headers={**AUTH, "If-None-Match": f"W/{response.headers['etag']}"})
    assert cached.status_code == 304 and not cached.content
    assert api.get("/v1/files", headers=AUTH, params={"ids": fid}).json()[0]["preview"] == preview(api, fid)


def test_text_cap_and_unsupported(api):
    fid = upload(api, "long.txt", ("字" * 210_000).encode())
    assert preview(api, fid)["truncated"] is True
    assert len(api.get(f"/v1/files/{fid}/preview/text.txt", headers=AUTH).text) == 200_000
    fid = upload(api, "unknown.bin")
    assert preview(api, fid) == {"status": "unsupported", "kind": None}


@pytest.mark.parametrize("name", ["document.pdf", "document.docx", "slides.pptx", "old.xls", "open.ods", "document.rtf"])
def test_pages_and_legacy_sheets_job_contract(api, name):
    fid = upload(api, name)
    p = ApiClient(api)
    assert preview(api, fid)["status"] == "pending"
    meta = p.get_file(fid)["meta"]
    process_preview(fid, meta["preview_job_id"], platform=p)
    ready = preview(api, fid)
    if name.endswith((".xls", ".ods")):
        assert ready == {"status": "ready", "kind": "sheets", "data": "sheets.json"}
        return
    assert ready["pages"] == ["p-01.jpg"] and ready["page_count"] == 57
    assert p.renders == 1
    process_preview(fid, meta["preview_job_id"], platform=p)
    assert p.renders == 1
    asset = api.get(f"/v1/files/{fid}/preview/p-01.jpg", headers=AUTH)
    assert asset.content == b"jpeg" and "immutable" in asset.headers["cache-control"]
    assert api.get(f"/v1/files/{fid}/preview/p-02.jpg", headers=AUTH).status_code == 404
    assert api.get(f"/v1/files/{fid}/preview/%2e%2e%2fsecret", headers=AUTH).status_code == 404


@pytest.mark.parametrize("ext,delimiter", [("csv", ","), ("tsv", "\t"), ("xlsx", None), ("xlsm", None)])
def test_sheets_pending_to_ready(api, ext, delimiter):
    if delimiter:
        data = f"姓名{delimiter}数量\nA{delimiter}12".encode()
    else:
        from openpyxl import Workbook

        wb = Workbook()
        wb.active.append(["姓名", "数量"])
        wb.active.append(["A", 12])
        out = io.BytesIO()
        wb.save(out)
        data = out.getvalue()
    fid = upload(api, f"sheet.{ext}", data)
    assert preview(api, fid) == {"status": "pending", "kind": "sheets"}
    p = ApiClient(api)
    process_preview(fid, p.get_file(fid)["meta"]["preview_job_id"], platform=p)
    assert preview(api, fid) == {"status": "ready", "kind": "sheets", "data": "sheets.json"}
    data = api.get(f"/v1/files/{fid}/preview/sheets.json", headers=AUTH).json()
    assert data["sheets"][0]["rows"][1][0]["v"] == "A"


def test_images_svg_and_lazy_existing_image(api):
    out = io.BytesIO()
    Image.new("RGB", (2400, 1200), "red").save(out, "JPEG")
    fid = upload(api, "photo.jpg", out.getvalue(), "image/jpeg")
    assert preview(api, fid) == {"status": "ready", "kind": "image", "image": "display.webp"}
    data = api.get(f"/v1/files/{fid}/preview/display.webp", headers=AUTH).content
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (2048, 1024)
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.meta = {"source": "sandbox"}
        db.commit()
    assert preview(api, fid)["status"] == "pending"
    p = ApiClient(api)
    process_preview(fid, p.get_file(fid)["meta"]["preview_job_id"], platform=p)
    assert preview(api, fid)["image"] == "display.webp"
    fid = upload(api, "vector.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "image/svg+xml")
    assert preview(api, fid)["image"] == "original"
    response = api.get(f"/v1/files/{fid}/preview/original", headers=AUTH)
    assert response.headers["content-type"] == "image/svg+xml" and "sandbox" in response.headers["content-security-policy"]


def test_failed_never_auto_retries_and_delete_cleans_every_derivative(api):
    fid = upload(api, "broken.xlsx", b"broken")
    assert preview(api, fid)["status"] == "pending"
    p = ApiClient(api)
    process_preview(fid, p.get_file(fid)["meta"]["preview_job_id"], platform=p)
    assert preview(api, fid)["status"] == "failed"
    assert p.get_file(fid)["status"] == "ready"
    before = get_queue().outstanding(settings.file_queue)
    assert preview(api, fid)["status"] == "failed"
    assert get_queue().outstanding(settings.file_queue) == before
    storage = get_storage()
    for name in ["preview/p-01.jpg", "display.webp", "other/future.dat"]:
        storage.put_bytes(f"derived/{fid}/{name}", b"x")
    assert api.delete(f"/v1/files/{fid}", headers=AUTH).status_code == 204  # to the trash: derivatives stay
    assert storage.list_prefix(f"derived/{fid}/").items
    assert api.delete("/v1/trash", headers=AUTH).status_code == 200  # purged for good
    assert storage.list_prefix(f"derived/{fid}/").items == []


def test_owned_ready_only(api):
    fid = upload(api, "pending.pdf", process=False)
    assert api.get(f"/v1/files/{fid}/preview", headers=AUTH).status_code == 404
    other = api.post("/v1/auth/register", json={"username": "preview-other", "password": "test-password-123"})
    assert other.status_code in {200, 201}
    other_auth = {"Authorization": "Bearer " + other.json()["token"]}
    # A second authenticated principal the pending and ready cases both hide existence.
    assert api.get(f"/v1/files/{fid}/preview", headers=other_auth).status_code == 404
    fid = upload(api, "ready.txt")
    assert api.get(f"/v1/files/{fid}/preview", headers=other_auth).status_code == 404


def test_concurrent_opens_keep_durable_pending_job(api, monkeypatch):
    monkeypatch.setattr(settings, "render_prewarm", 0)
    fid = upload(api, "a.pdf")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: preview(api, fid), range(8)))
    assert all(p["status"] == "pending" for p in results)
    jobs = [m for m in get_queue().outstanding(job_topic("preview")) if m.get("kind") == "preview" and m["file_id"] == fid]
    assert len(jobs) == 1
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.meta = {**file.meta, "preview_requested_at": datetime.now(UTC).timestamp() - 301}
        db.commit()
    preview(api, fid)
    jobs = [m for m in get_queue().outstanding(job_topic("preview")) if m.get("kind") == "preview" and m["file_id"] == fid]
    assert len(jobs) == 1


def test_outbox_survives_queue_failure_and_dispatch_retry(api, monkeypatch):
    from sqlalchemy import select

    from ailab_platform.api.preview_dispatch import dispatch_preview_jobs
    from ailab_platform.models import OutboxMessage

    monkeypatch.setattr(settings, "preview_eager_max_bytes", 0)
    fid = upload(api, "outbox.pdf")
    q = get_queue()
    enqueue = q.enqueue

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("queue down")

    monkeypatch.setattr(q, "enqueue", unavailable)
    assert preview(api, fid)["status"] == "pending"
    with SessionLocal() as db:
        message = db.scalar(
            select(OutboxMessage).where(
                OutboxMessage.payload["file_id"].as_string() == fid, OutboxMessage.payload["kind"].as_string() == "preview"
            )
        )
        assert message is not None
        job_id = message.id
    monkeypatch.setattr(q, "enqueue", enqueue)
    from datetime import timedelta

    dispatch_preview_jobs(job_id, now=datetime.now(UTC) + timedelta(seconds=5))
    assert any(m.get("job_id") == job_id for m in q.outstanding(job_topic("preview")))
    with SessionLocal() as db:
        assert db.get(OutboxMessage, job_id) is not None  # publication is not consumer ACK


def test_worker_preview_concurrent_messages_render_once(api, monkeypatch):
    import threading

    from ailab_platform.worker import preview as worker

    fid = upload(api, "simultaneous.pdf")
    preview(api, fid)
    p = ApiClient(api)
    job_id = p.get_file(fid)["meta"]["preview_job_id"]
    entered, release = threading.Event(), threading.Event()
    render = p.render_preview

    def waiting(*args):
        entered.set()
        assert release.wait(5)
        return render(*args)

    monkeypatch.setattr(p, "render_preview", waiting)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker.process_preview, fid, job_id, p)
        assert entered.wait(5)
        second = pool.submit(worker.process_preview, fid, job_id, p)
        assert second.result(timeout=5) == {"skipped": "in_flight"}
        release.set()
        assert first.result(timeout=5)["status"] == "ready"
    assert p.renders == 1


def test_preview_poll_never_reads_storage(api, monkeypatch):
    fid = upload(api, "no-read.pdf")
    storage = get_storage()

    def unexpected(*args, **kwargs):
        pytest.fail("polling must read only metadata")

    for method in ["get_bytes", "stream", "exists", "size", "list_prefix"]:
        monkeypatch.setattr(storage, method, unexpected)
    assert preview(api, fid)["status"] == "pending"
    assert preview(api, fid)["status"] == "pending"


def test_preview_asset_disconnect_closes_stream(api, monkeypatch):
    import anyio

    from ailab_platform.api.routes_files import preview_asset
    from ailab_platform.models import User

    fid = upload(api, "close.txt")
    preview(api, fid)
    storage = get_storage()
    closed = []

    def stream(*args, **kwargs):
        try:
            while True:
                yield b"x"
        finally:
            closed.append(True)

    monkeypatch.setattr(storage, "stream", stream)
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        response = preview_asset(fid, "text.txt", if_none_match=None, user=db.get(User, file.user_id), db=db)

    async def disconnect():
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("disconnected")

    async def run():
        from starlette.requests import ClientDisconnect

        with pytest.raises(ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, disconnect, send)

    anyio.run(run)
    assert closed == [True]


@pytest.mark.parametrize("tamper", ["path", "missing_asset", "bad_digest", "bad_mime", "wrong_job"])
def test_preview_completion_rejects_untrusted_manifest(api, tamper):
    fid = upload(api, "guard.pdf")
    preview(api, fid)
    p = ApiClient(api)
    job_id = p.get_file(fid)["meta"]["preview_job_id"]
    result = p.render_preview(fid, job_id)
    body = {"job_id": job_id, **result}
    if tamper == "path":
        body["assets"]["p-01.jpg"]["key"] = "uploads/another-user/secret"
    elif tamper == "missing_asset":
        body["assets"] = {}
    elif tamper == "bad_digest":
        body["assets"]["p-01.jpg"]["sha256"] = "not-a-hash"
    elif tamper == "bad_mime":
        body["assets"]["p-01.jpg"]["content_type"] = "application/json"
    elif tamper == "wrong_job":
        body["job_id"] = "wrong"
    response = api.post(f"/internal/files/{fid}/preview", headers=SERVICE, json=body)
    assert response.status_code in {400, 409, 422}
    assert preview(api, fid)["status"] == "pending"


def test_eager_pages_are_available_to_visual_requests_without_another_render(api):
    from ailab_platform.api.routes_library_tools import document_images

    fid = upload(api, "eager.pdf")
    job = ApiClient(api).get_file(fid)["meta"]["preview_job_id"]
    process_preview(fid, job, platform=ApiClient(api))
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        result = document_images(db, file, [1])
        assert [page["page"] for page in result["files"]] == [1]
        assert file.meta["preview_job_id"] == job


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "gb18030"])
def test_text_artifact_without_extracted_text_gets_a_preview(api, encoding):
    api.headers.update(AUTH)
    from tests.run_claims import attempt_headers
    from tests.test_projects import artifact

    run = api.post("/v1/runs", json={"prompt": "文字产物"}).json()
    api.post(f"/internal/runs/{run['id']}/status", headers=attempt_headers(api, run["id"]), json={"status": "running"})
    file = artifact(api, run["id"], "中文第一行\n第二行".encode(encoding))
    result = api.get(f"/v1/files/{file['file_id']}/preview")
    assert result.status_code == 200 and result.json()["status"] == "pending"
    current = api.get(f"/v1/files/{file['file_id']}").json()
    process_preview(file["file_id"], current["meta"]["preview_job_id"], ApiClient(api))
    assert api.get(f"/v1/files/{file['file_id']}/preview").json()["status"] == "ready"
    text = api.get(f"/v1/files/{file['file_id']}/preview/text.txt")
    assert text.text == "中文第一行\n第二行"
