"""Real isolated parsing and controlled HTTP inference, never paid endpoints."""

import io
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.catalog import ModelSpec, get_catalog
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, FileStatus, ModelUsage, OutboxMessage, UsageReservation, new_id
from ailab_platform.storage import get_storage
from ailab_platform.worker.ocr import process_ocr
from ailab_platform.worker.pipeline import process_file
from tests.test_file_previews import AUTH, SERVICE, ApiClient


class OCRClient(ApiClient):
    def ocr(self, fid, prepare=False):
        r = self.api.post(f"/internal/files/{fid}/ocr" + ("/prepare" if prepare else ""), headers=SERVICE, json={})
        r.raise_for_status()
        return r.json()

    def close(self):
        pass


@pytest.fixture
def upstream(monkeypatch):
    seen = []
    reply = {"text": "发票 金额 123 元", "status": 200, "usage": True}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((dict(self.headers), payload))
            body = {"model": "vision-local", "choices": [{"message": {"content": reply["text"]}, "finish_reason": "stop"}]}
            if reply["usage"]:
                body["usage"] = {"prompt_tokens": 100, "completion_tokens": 20}
            encoded = json.dumps(body).encode()
            self.send_response(reply["status"])
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    alias = new_id("vision")
    monkeypatch.setitem(
        get_catalog().models,
        alias,
        ModelSpec(id=alias, name=alias, base_url=f"http://127.0.0.1:{server.server_port}/v1", capabilities=["vision"]),
    )
    monkeypatch.setattr(settings, "ocr_alias", alias)
    yield seen, reply
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture
def api():
    with TestClient(app) as c:
        yield c


def photo():
    output = io.BytesIO()
    image = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(image).text((20, 50), "Invoice amount 123", fill="black", font_size=24)
    image.save(output, "PNG")
    return output.getvalue()


def blank_pdf(pages):
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(200, 200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def text_pdf(pages):
    """Hand-written PDF with native text on every page (no PDF writer dependency)."""
    font = 3 + 2 * pages
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{' '.join(f'{3 + 2 * i} 0 R' for i in range(pages))}] /Count {pages} >>".encode(),
        font: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for i in range(pages):
        content = f"BT /F1 12 Tf 20 150 Td (This native text page {i + 1} carries enough characters to count.) Tj ET".encode()
        objects[3 + 2 * i] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << /Font << /F1 {font} 0 R >> >> /Contents {4 + 2 * i} 0 R >>"
        ).encode()
        objects[4 + 2 * i] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for number in range(1, len(objects) + 1):
        out += b"%010d 00000 n \n" % offsets[number]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def upload(api, name="photo.png", data=None, mime="image/png"):
    r = api.post("/v1/files", headers=AUTH, files={"upload": (name, data or photo(), mime)})
    assert r.status_code == 201
    return r.json()["id"]


def queued_ocr(fid):
    with SessionLocal() as db:
        return [m.payload for m in db.scalars(select(OutboxMessage)) if m.payload.get("file_id") == fid and m.payload.get("ocr_only")]


def run_ocr_job(api, fid, client=None):
    """What the Worker does for the queued ocr_only message after the scan completed."""
    return process_ocr(fid, client or OCRClient(api))


def test_scan_queues_ocr_in_the_ready_transaction_and_never_infers_inline(api, upstream):
    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    assert upstream[0] == []
    assert queued_ocr(fid) == [{"file_id": fid, "ocr_only": True}]
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.status == FileStatus.READY
        assert file.meta["ocr"] == "pending" and file.meta["ocr_requested_at"] > 0


def test_image_ocr_bills_file_owner_and_is_not_replayed(api, upstream):
    from ailab_platform.models import User

    fid = upload(api)
    with SessionLocal() as db:
        owner_id = new_id("usr")
        db.add(User(id=owner_id, email=owner_id + "@example.test", display_name="OCR owner"))
        db.flush()
        db.get(FileObject, fid).user_id = owner_id
        db.commit()
    process_file(fid, platform=OCRClient(api))
    run_ocr_job(api, fid)
    seen, _ = upstream
    assert len(seen) == 1
    headers, payload = seen[0]
    image = payload["messages"][0]["content"][1]["image_url"]
    assert image["url"].startswith("data:image/webp;base64,") and image["detail"] == "high"
    assert "x-run-id" not in headers
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.meta["ocr"] == "done"
        assert "123" in get_storage().get_bytes(file.meta["ocr_text_key"]).decode()
        assert "text_key" not in file.meta  # the Worker's native extraction is never overwritten
        usage = db.scalar(select(ModelUsage).where(ModelUsage.file_id == fid))
        assert (usage.user_id, usage.run_id, usage.mode, usage.subject) == (file.user_id, None, "ocr", "file_job")
        assert usage.weighted_amount == 120000
        assert headers["x-user-id"] == file.user_id
    denied = api.post("/internal/usage/settle", headers=SERVICE, json={"call_id": f"ocr:{fid}:0", "status": "released"})
    assert denied.status_code == 403
    run_ocr_job(api, fid)
    assert len(seen) == 1


def test_unconfigured_is_explicit_unavailable_and_never_queued(api):
    fid = upload(api)
    with patch.object(settings, "ocr_alias", ""):
        process_file(fid, platform=OCRClient(api))
    with SessionLocal() as db:
        meta = db.get(FileObject, fid).meta
        assert (meta["ocr"], meta["ocr_reason"]) == ("unavailable", "vision_not_configured")
        assert "ocr_pdf" not in meta
        assert db.scalar(select(ModelUsage.id).where(ModelUsage.file_id == fid)) is None
    assert queued_ocr(fid) == []


def test_busy_inference_slot_leaves_the_job_pending_and_the_file_ready(api, upstream):
    from ailab_platform.api.routes_ocr import _ocr_slot

    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    _ocr_slot.acquire()
    try:
        with pytest.raises(httpx.HTTPStatusError) as busy:
            run_ocr_job(api, fid)
        assert busy.value.response.status_code == 429
    finally:
        _ocr_slot.release()
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.status == FileStatus.READY and file.meta["ocr"] == "pending"
        assert file.meta.get("processing") != "error"
    run_ocr_job(api, fid)
    assert len(upstream[0]) == 1


def test_ocr_message_acks_on_completion_and_redelivers_while_busy(api, upstream, monkeypatch):
    from ailab_platform.api.routes_ocr import _ocr_slot
    from ailab_platform.queue import InMemoryQueue
    from ailab_platform.worker import main as worker_main

    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    monkeypatch.setattr(worker_main, "PlatformClient", lambda **kwargs: OCRClient(api))
    queue = InMemoryQueue()
    queue.enqueue(settings.file_queue, {"file_id": fid, "ocr_only": True})
    _ocr_slot.acquire()
    try:
        worker_main.handle_message(queue, queue.dequeue(settings.file_queue, "w", timeout=1, visibility_seconds=0))
    finally:
        _ocr_slot.release()
    assert queue.outstanding(settings.file_queue) == [{"file_id": fid, "ocr_only": True}]
    worker_main.handle_message(queue, queue.dequeue(settings.file_queue, "w", timeout=1, visibility_seconds=0))
    assert queue.outstanding(settings.file_queue) == []
    assert len(upstream[0]) == 1


@pytest.mark.parametrize("status,text,usage,expected", [(500, "secret upstream body", False, "estimated"), (200, "", True, "settled")])
def test_failure_billed_and_not_replayed(api, upstream, status, text, usage, expected):
    seen, reply = upstream
    reply.update(status=status, text=text, usage=usage)
    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    run_ocr_job(api, fid)
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.meta["ocr"] == "failed"
        assert "secret" not in str(file.meta)
        assert db.get(UsageReservation, f"ocr:{fid}:0").status == expected
    OCRClient(api).ocr(fid, prepare=True)
    assert len(seen) == 1


def test_scanned_pdf_uses_preview_pages_and_only_low_text_pages(api, upstream):
    fid = upload(api, "scan.pdf", blank_pdf(1), "application/pdf")
    process_file(fid, platform=OCRClient(api))
    run_ocr_job(api, fid)
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.meta["ocr_pdf"] == {"pages": 1, "low_text_pages": [0]}
        assert file.meta["preview_job_id"]
        assert file.meta["preview"]["status"] == "ready"
        assert file.meta["ocr"] == "done"
        assert "发票" in get_storage().get_bytes(file.meta["ocr_text_key"]).decode()
    assert upstream[0][0][1]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg")


def test_text_pdf_over_page_limit_needs_no_inference(api, upstream, monkeypatch):
    monkeypatch.setattr(settings, "ocr_max_pages", 1)
    fid = upload(api, "text.pdf", text_pdf(2), "application/pdf")
    process_file(fid, platform=OCRClient(api))
    run_ocr_job(api, fid)
    with SessionLocal() as db:
        meta = db.get(FileObject, fid).meta
        assert (meta["ocr"], meta["ocr_reason"]) == ("done", "text_pdf")
        assert meta["ocr_pdf"]["low_text_pages"] == []
    assert upstream[0] == []


def test_scanned_pages_over_limit_fail_explicitly_before_inference(api, upstream, monkeypatch):
    monkeypatch.setattr(settings, "ocr_max_pages", 1)
    fid = upload(api, "long-scan.pdf", blank_pdf(2), "application/pdf")
    process_file(fid, platform=OCRClient(api))
    run_ocr_job(api, fid)
    with SessionLocal() as db:
        meta = db.get(FileObject, fid).meta
        assert (meta["ocr"], meta["ocr_reason"]) == ("failed", "page_limit")
    assert upstream[0] == []


def test_pdf_is_not_analysed_when_ocr_is_unconfigured(api):
    fid = upload(api, "scan.pdf", blank_pdf(1), "application/pdf")
    with patch.object(settings, "ocr_alias", ""):
        process_file(fid, platform=OCRClient(api))
    with SessionLocal() as db:
        meta = db.get(FileObject, fid).meta
        assert meta["ocr"] == "unavailable" and "ocr_pdf" not in meta


def test_shared_user_quota_blocks_inference(api, upstream, monkeypatch):
    from ailab_platform import quota
    from ailab_platform.domain.quota_policy import Limits

    monkeypatch.setattr(quota, "user_limits", lambda db, uid: Limits(1, 1, 100_000_000, 10))
    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    run_ocr_job(api, fid)
    assert upstream[0] == []
    with SessionLocal() as db:
        assert db.get(FileObject, fid).meta["ocr"] == "failed"
        assert db.get(UsageReservation, f"ocr:{fid}:0") is None


def test_size_limit_fails_before_inference(api, upstream, monkeypatch):
    monkeypatch.setattr(settings, "ocr_max_bytes", 1)
    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    run_ocr_job(api, fid)
    with SessionLocal() as db:
        assert db.get(FileObject, fid).meta["ocr_reason"] == "size_limit"
    assert upstream[0] == []


def test_stale_pending_job_fails_at_deadline_without_inference(api, upstream, monkeypatch):
    monkeypatch.setattr(settings, "ocr_deadline_seconds", 60)
    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.meta = {**file.meta, "ocr_requested_at": datetime.now(UTC).timestamp() - 61}
        db.commit()
    run_ocr_job(api, fid)
    with SessionLocal() as db:
        meta = db.get(FileObject, fid).meta
        assert (meta["ocr"], meta["ocr_reason"]) == ("failed", "deadline")
    assert upstream[0] == []


def test_ocr_scope_is_worker_only_and_scan_required(api, monkeypatch):
    import hashlib

    monkeypatch.setattr(
        settings,
        "service_credentials",
        {name: "sha256:" + hashlib.sha256((name + "-ocr-test").encode()).hexdigest() for name in ("worker", "agent", "broker")},
    )
    fid = upload(api)
    for name in ("agent", "broker"):
        assert api.post(f"/internal/files/{fid}/ocr/prepare", headers={"X-Service-Token": name + "-ocr-test"}).status_code == 403
    assert api.post(f"/internal/files/{fid}/ocr/prepare", headers={"X-Service-Token": "worker-ocr-test"}).status_code == 409
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.status = FileStatus.READY
        file.meta = {"scan": {"verdict": "infected"}}
        db.commit()
    assert api.post(f"/internal/files/{fid}/ocr/prepare", headers={"X-Service-Token": "worker-ocr-test"}).status_code == 409


def test_scanned_pdf_real_broker_renderer(api, upstream, tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    from ailab_platform.broker.render import LibraryRenderer
    from tests.test_workspace_previews import _manager

    if not all(shutil.which(x) for x in ("pdftoppm", "pdfinfo", "timeout")):
        pytest.skip("PDF renderer prerequisites unavailable")
    buffer = io.BytesIO()
    with Image.open(io.BytesIO(photo())) as image:
        image.save(buffer, "PDF")
    fid = upload(api, "scan-real.pdf", buffer.getvalue(), "application/pdf")
    client = OCRClient(api)
    manager, _, _ = _manager(tmp_path)
    manager.storage = get_storage()

    def get_file(file_id, **bound):
        response = api.get(f"/internal/files/{file_id}", params=bound, headers=SERVICE)
        response.raise_for_status()
        return response.json()

    def download(file_id, target, **bound):
        bound.pop("max_bytes", None)
        response = api.get(f"/internal/files/{file_id}/content", params=bound, headers=SERVICE)
        response.raise_for_status()
        Path(target).write_bytes(response.content)

    manager.platform.get_file = get_file
    manager.platform.download_file = download
    monkeypatch.setattr(client, "render_preview", LibraryRenderer(manager).render)
    process_file(fid, platform=client)
    run_ocr_job(api, fid, client)
    assert manager.active_leases() == []
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.meta["ocr"] == "done"
        asset = next(iter(file.meta["preview_assets"].values()))
        assert get_storage().get_bytes(asset["key"]).startswith(b"\xff\xd8")
    assert len(upstream[0]) == 1


def test_conversation_image_is_bound_to_attached_active_run(api):
    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    run = api.post("/v1/runs", headers=AUTH, json={"prompt": "看图", "file_ids": [fid]}).json()
    r = api.get(f"/internal/files/{fid}/vision", params={"run_id": run["id"]}, headers=SERVICE)
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    other = api.post("/v1/runs", headers=AUTH, json={"prompt": "unattached"}).json()
    # Legacy shared token deliberately bypasses resource binding; use the actual
    # agent identity to exercise the secure path.
    import hashlib

    credentials = {"agent": "sha256:" + hashlib.sha256(b"agent-image-test").hexdigest()}
    with patch.object(settings, "service_credentials", credentials):
        headers = {"X-Service-Token": "agent-image-test"}
        assert api.get(f"/internal/files/{fid}/vision", params={"run_id": other["id"]}, headers=headers).status_code == 404
        assert api.get(f"/internal/files/{fid}/vision", params={"run_id": run["id"]}, headers=headers).status_code == 200


def test_attachment_text_for_the_model_is_the_ocr_text(api, upstream):
    from ailab_platform.api.routes_internal import _attachment_texts
    from ailab_platform.models import Run

    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    run_ocr_job(api, fid)
    run = api.post("/v1/runs", headers=AUTH, json={"prompt": "看图", "file_ids": [fid]}).json()
    with SessionLocal() as db:
        texts = _attachment_texts(db, db.get(Run, run["id"]))
    assert "123" in texts[0]["text"] and texts[0]["meta"]["ocr"] == "done"


def test_interrupted_ocr_is_explicit_failed_and_reservation_reaped(api, upstream):
    from datetime import timedelta

    from ailab_platform.contracts.usage import ReserveUsageIn
    from ailab_platform.quota import expire_reservations, reserve_usage

    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        reserve_usage(
            db,
            ReserveUsageIn(
                call_id=f"ocr:{fid}:0",
                subject="file_job",
                file_id=fid,
                alias=settings.ocr_alias,
                attempt_id="crash",
                mode="ocr",
                max_output_tokens=100,
                estimated_input_tokens=100,
            ),
        )
        file.meta = {**file.meta, "ocr": "running", "ocr_started_at": 0}
        reservation = db.get(UsageReservation, f"ocr:{fid}:0")
        reservation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    run_ocr_job(api, fid)
    with SessionLocal() as db:
        assert db.get(FileObject, fid).meta["ocr"] == "failed"
        assert db.get(FileObject, fid).meta["ocr_reason"] == "interrupted"
        expire_reservations(db, datetime.now(UTC))
        row = db.scalar(select(ModelUsage).where(ModelUsage.file_id == fid))
        assert row.status == "unreconciled" and row.weighted_amount == 200000
        assert row.run_id is None and row.mode == "ocr"
    assert upstream[0] == []


def test_file_usage_subject_is_validated():
    from pydantic import ValidationError

    from ailab_platform.contracts.usage import ReserveUsageIn

    with pytest.raises(ValidationError):
        ReserveUsageIn(call_id="x", alias="a", mode="ocr", run_id="r", attempt_id="a", max_output_tokens=1, estimated_input_tokens=1)
    with pytest.raises(ValidationError):
        ReserveUsageIn(
            call_id="x",
            alias="a",
            mode="chat",
            subject="file_job",
            file_id="f",
            attempt_id="a",
            max_output_tokens=1,
            estimated_input_tokens=1,
        )


def test_multimodal_fallbacks_are_explicit_and_images_count_as_budgeted_tokens():
    from ailab_platform.agent.multimodal import attachment_content, has_image_part, image_part
    from ailab_platform.model_policy import estimator

    photo_file = {"id": "f1", "filename": "photo.png", "content_type": "image/png", "meta": {"ocr": "unavailable"}}
    assert "图片未能读取（OCR unavailable）" in attachment_content(photo_file, vision=False)[0]["text"]
    empty_docx = {"id": "f2", "filename": "empty.docx", "content_type": "application/vnd", "meta": {}}
    text = attachment_content(empty_docx, vision=False)[0]["text"]
    assert "图片" not in text and "无法提取文字" in text
    assert "recognized" in attachment_content(photo_file, vision=False, text="recognized")[0]["text"]
    parts = attachment_content(photo_file, vision=True, image=photo(), content_type="image/png")
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["detail"] == "high"
    assert has_image_part([{"role": "user", "content": parts}])
    assert not has_image_part([{"role": "user", "content": "plain text"}])
    assert settings.vision_input_tokens <= estimator.raw([{"role": "user", "content": parts}]) < settings.vision_input_tokens + 200
    with pytest.raises(ValueError, match="unsupported_image"):
        image_part(b"x", "image/svg+xml")
