"""Real isolated parsing and controlled HTTP inference, never paid endpoints."""

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.catalog import ModelSpec, get_catalog
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, ModelUsage, OutboxMessage, UsageReservation, new_id
from ailab_platform.worker.ocr import process_ocr
from ailab_platform.worker.pipeline import process_file
from tests.test_file_previews import AUTH, SERVICE, ApiClient


class OCRClient(ApiClient):
    def job_action(self, job_id, action, **body):
        r = self.api.post(f"/internal/jobs/{job_id}/{action}", headers=SERVICE, json=body)
        r.raise_for_status()
        return r.json()

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
    monkeypatch.setattr(settings, "context_summary_alias", alias)
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
        return [m.payload for m in db.scalars(select(OutboxMessage)) if m.payload.get("file_id") == fid and m.payload.get("kind") == "ocr"]


def run_ocr_job(api, fid, client=None):
    """What the Worker does for the queued ocr_only message after the scan completed."""
    return process_ocr(fid, client or OCRClient(api))


def test_pdf_is_not_analysed_when_ocr_is_unconfigured(api):
    fid = upload(api, "scan.pdf", blank_pdf(1), "application/pdf")
    with patch.object(settings, "ocr_alias", ""):
        process_file(fid, platform=OCRClient(api))
    with SessionLocal() as db:
        meta = db.get(FileObject, fid).meta
        assert meta["ocr"] == "unavailable" and "ocr_pdf" not in meta


def test_conversation_image_is_bound_to_attached_active_run(api, rapid_upstream):
    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    api.post(f"/v1/files/{fid}/extract-text", headers=AUTH, json={})
    run_ocr_job(api, fid)
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


def test_prior_turn_image_is_available_only_on_its_authorized_branch(api, rapid_upstream):
    import hashlib

    from tests.test_conversations import _finish

    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    api.post(f"/v1/files/{fid}/extract-text", headers=AUTH, json={})
    run_ocr_job(api, fid)
    conv = api.post("/v1/conversations", headers=AUTH, json={}).json()
    response = api.post(
        f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "看图", "file_ids": [fid], "mode": "chat"}
    )
    assert response.status_code == 201, response.text
    first = response.json()
    _finish(api, first["id"], "done")
    child = api.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "continue"}).json()
    other = api.post("/v1/runs", headers=AUTH, json={"prompt": "other"}).json()
    credentials = {"agent": "sha256:" + hashlib.sha256(b"agent-image-test").hexdigest()}
    with patch.object(settings, "service_credentials", credentials):
        headers = {"X-Service-Token": "agent-image-test"}
        assert api.get(f"/internal/files/{fid}/vision", params={"run_id": child["id"]}, headers=headers).status_code == 200
        assert api.get(f"/internal/files/{fid}/vision", params={"run_id": other["id"]}, headers=headers).status_code == 404
        assert api.delete(f"/v1/files/{fid}", headers=AUTH).status_code == 204
        assert api.get(f"/internal/files/{fid}/vision", params={"run_id": child["id"]}, headers=headers).status_code == 404


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


@pytest.mark.parametrize("vision,available", [(True, True), (True, False), (False, False)])
def test_agent_assembles_image_input_with_budgeted_usage_and_explicit_fallback(monkeypatch, vision, available):
    import asyncio

    from ailab_platform.agent.loop import AgentLoop
    from ailab_platform.catalog import Catalog
    from ailab_platform.llm import LLMResponse
    from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM

    class Platform(FakePlatform):
        reads = 0

        async def get_run(self, run_id):
            return {
                **await super().get_run(run_id),
                "model": "image-test",
                "options": {"mode": "chat"},
                "input_files": [{"id": "image", "filename": "photo.png", "content_type": "image/png", "meta": {"ocr": "unavailable"}}],
            }

        async def get_file_image(self, file_id, run_id):
            self.reads += 1
            assert (file_id, run_id) == ("image", "run_1")
            if not available:
                raise ValueError("image_size_limit")
            return b"x" * 100_000

    catalog = Catalog([ModelSpec("image-test", "Image", provider="mock", capabilities=["vision"] if vision else [])])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: catalog)
    platform, broker = Platform(), FakeBroker()
    llm = ScriptedLLM([LLMResponse(content="done")])
    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt_id="attempt"))
    assert platform.statuses[-1][0] == "succeeded"
    parts = llm.seen_messages[0][-1]["content"]
    assert any(part["type"] == "image_url" for part in parts) == (vision and available)
    if not (vision and available):
        assert any("图片未能读取" in part.get("text", "") for part in parts)
    assert platform.reads == int(vision)
    assert broker.leases == 0
    reservation = next(iter(platform.reservations.values()))
    assert reservation["estimated_input_tokens"] < settings.vision_input_tokens + 10_000
    if vision and available:
        saved = platform.checkpoint["state"]
        assert any(part["type"] == "ailab_image" for part in saved["messages"][2]["content"])
        assert len(json.dumps(saved)) < 10_000
        saved["finished"] = False
        resumed = ScriptedLLM([LLMResponse(content="resumed")])
        asyncio.run(AgentLoop(platform=platform, broker=broker, llm=resumed).process("run_1", attempt=2, attempt_id="next"))
        assert platform.reads == 2
        assert any(part["type"] == "image_url" for part in resumed.seen_messages[0][2]["content"])


def test_offline_model_accepts_multimodal_text_fallback():
    import asyncio

    from ailab_platform.llm import MockLLM

    messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "图片未能读取"}]}]
    response = asyncio.run(MockLLM().chat(messages, [], "test-fast"))
    assert "hello" in response.content

    # No UI changes here: the Portal's existing file citation handler receives this exact page=2.


@pytest.fixture
def rapid_upstream(monkeypatch):
    from pydantic import SecretStr

    seen = []
    reply = {"text": "发票 金额 123 元", "backend": "cann", "engine": "rapidocr:80b427d", "seconds": 0.12}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(payload)
            encoded = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "ocr_executor_url", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(settings, "ocr_executor_token", SecretStr("isolated-test"))
    yield seen, reply
    server.shutdown()
    thread.join()
    server.server_close()


def test_upload_never_queues_paid_vision_ocr(api, upstream):
    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    assert not queued_ocr(fid) and not upstream[0]
    response = api.post(f"/v1/files/{fid}/extract-text", headers=AUTH, json={})
    assert response.status_code == 503
    with SessionLocal() as db:
        assert db.get(FileObject, fid).status == "ready"


def test_rapidocr_metering_without_billing_and_duplicate_delivery(api, rapid_upstream):
    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    assert api.post(f"/v1/files/{fid}/extract-text", headers=AUTH, json={}).status_code == 200
    assert len(queued_ocr(fid)) == 1
    assert run_ocr_job(api, fid)["ocr"] == "done"
    assert run_ocr_job(api, fid)["ocr"] == "done"
    assert len(rapid_upstream[0]) == 1
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.meta["ocr_pages"]["0"]["backend"] == "cann"
        assert file.meta["ocr_pages"]["0"]["seconds"] == 0.12
        assert not db.scalar(select(ModelUsage).where(ModelUsage.file_id == fid))
        assert not db.scalar(select(UsageReservation).where(UsageReservation.file_id == fid))


def test_cpu_response_never_counts_as_npu_success(api, rapid_upstream):
    rapid_upstream[1]["backend"] = "cpu"
    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    api.post(f"/v1/files/{fid}/extract-text", headers=AUTH, json={})
    assert run_ocr_job(api, fid)["ocr"] == "failed"
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.meta["ocr_reason"] == "ocr_backend_mismatch"
        assert file.status == "ready"
        assert "ocr_text_key" not in file.meta


def test_nonvision_chat_prepares_image_before_creating_run(api, rapid_upstream):
    fid = upload(api)
    process_file(fid, platform=OCRClient(api))
    body = {"prompt": "提取金额", "mode": "chat", "file_ids": [fid]}
    response = api.post("/v1/runs", headers=AUTH, json=body)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "chat_attachment_processing"
    run_ocr_job(api, fid)
    run = api.post("/v1/runs", headers=AUTH, json=body)
    assert run.status_code == 201
    files = api.get(f"/internal/runs/{run.json()['id']}/model-input", headers=SERVICE).json()["input_files"]
    assert "123" in files[0]["text"]


def test_pdf_extraction_requires_explicit_range_and_preserves_native_input(api, rapid_upstream):
    fid = upload(api, "range.pdf", text_pdf(100), "application/pdf")
    process_file(fid, platform=OCRClient(api))
    assert api.post(f"/v1/files/{fid}/extract-text", headers=AUTH, json={}).status_code == 422
    assert api.post(f"/v1/files/{fid}/extract-text", headers=AUTH, json={"pages": [85]}).status_code == 200
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.meta["ocr_pdf"]["low_text_pages"] == [84]
        assert file.meta["extract"]["units"] == 100
