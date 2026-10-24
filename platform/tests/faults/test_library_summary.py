"""Durable summary redelivery and dependency-failure acceptance, with real API billing."""

from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.api.routes_library_summary import request_summary
from ailab_platform.db import SessionLocal
from ailab_platform.llm import LLMResponse, model_call
from ailab_platform.models import FileObject, ModelUsage, OutboxMessage, Run, UsageReservation
from ailab_platform.queue import Message
from ailab_platform.worker.main import handle_message
from tests.test_conversations import AUTH
from tests.test_ocr import OCRClient


def summary_file(c, text="资料的主题与要点。" * 100):
    run = c.post("/v1/runs", headers=AUTH, json={"prompt": "owner"}).json()
    with SessionLocal() as db:
        f = FileObject(
            user_id=db.get(Run, run["id"]).user_id,
            filename=f"summary-{uuid4().hex}.txt",
            storage_key="unused",
            status="ready",
            meta={"excerpt": text},
        )
        db.add(f)
        db.flush()
        request_summary(db, f)
        db.commit()
        return f.id, f.meta["summary"]["job_id"]


class SummaryClient(OCRClient):
    def summarize_file(self, fid):
        response = self.api.post(f"/internal/files/{fid}/summary", headers={"X-Service-Token": "test-service"})
        response.raise_for_status()
        return response.json()


class Queue:
    acks = 0

    def ack(self, *args):
        self.acks += 1


def deliver(c, job):
    with SessionLocal() as db:
        payload = dict(db.get(OutboxMessage, job).payload)
    msg = Message("delivery", payload)
    queue = Queue()
    with patch("ailab_platform.worker.main.PlatformClient", lambda **kw: SummaryClient(c)):
        handle_message(queue, msg)
    return queue, msg


class Model:
    def __init__(self):
        self.calls = []
        self.down = False

    async def chat(self, messages, tools, alias, user_id=None):
        self.calls.append((model_call.get(), messages, user_id))
        if self.down:
            raise ConnectionError("upstream unavailable")
        return LLMResponse(content="资料概要", usage={"prompt_tokens": 100, "completion_tokens": 10})


def test_summary_redelivered_one_call_quiet_owner_pricing_and_ack(monkeypatch):
    with TestClient(app) as c:
        fid, job = summary_file(c, "长资料" * 10000)
        model = Model()
        monkeypatch.setattr("ailab_platform.api.routes_library_summary.llm_for", lambda alias: model)
        queue, msg = deliver(c, job)
        assert queue.acks == 1
        with patch("ailab_platform.worker.main.PlatformClient", lambda **kw: SummaryClient(c)):
            handle_message(queue, msg)
        assert queue.acks == 2 and len(model.calls) == 1
        call, messages, owner = model.calls[0]
        assert call.call_id == f"summary:{fid}" and call.quiet and call.max_output_tokens <= 200
        from ailab_platform.model_policy import estimator

        with SessionLocal() as db:
            f = db.get(FileObject, fid)
            assert f.meta["summary"]["status"] == "done" and f.meta["summary"]["text"] == "资料概要"
            usage = db.scalar(select(ModelUsage).where(ModelUsage.call_id == call.call_id))
            reservation = db.get(UsageReservation, call.call_id)
            assert usage.user_id == f.user_id == owner and usage.file_id == fid and usage.run_id is None
            assert usage.mode == "summary" and usage.quiet and usage.policy_version == reservation.policy_version
            assert usage.weight == float(reservation.weight) and usage.prompt_tokens == 100
            assert estimator.estimate(usage.alias, messages) <= 6000
            assert db.get(OutboxMessage, job) is None


def test_summary_model_down_failed_then_later_retry(monkeypatch):
    with TestClient(app) as c:
        fid, job = summary_file(c)
        model = Model()
        model.down = True
        monkeypatch.setattr("ailab_platform.api.routes_library_summary.llm_for", lambda alias: model)
        queue, msg = deliver(c, job)
        assert queue.acks == 0
        with SessionLocal() as db:
            assert db.get(FileObject, fid).meta["summary"]["status"] == "failed"
            pending = db.get(OutboxMessage, job)
            assert pending is not None
            pending.claimed_until = None
            db.commit()
        model.down = False
        with patch("ailab_platform.worker.main.PlatformClient", lambda **kw: SummaryClient(c)):
            handle_message(queue, msg)
        assert queue.acks == 1 and len(model.calls) == 2
        assert model.calls[1][0].call_id == f"summary:{fid}:retry:1"
        with SessionLocal() as db:
            assert db.get(FileObject, fid).meta["summary"]["status"] == "done"


def test_summary_dependency_failure_has_bounded_retries(monkeypatch):
    with TestClient(app) as c:
        fid, job = summary_file(c)
        model = Model()
        model.down = True
        monkeypatch.setattr("ailab_platform.api.routes_library_summary.llm_for", lambda alias: model)
        for attempt in range(1, 4):
            response = c.post(f"/internal/files/{fid}/summary", headers={"X-Service-Token": "test-service"})
            assert response.status_code == 200 and response.json()["status"] == "failed"
            assert response.json()["attempt"] == attempt
        assert c.post(f"/internal/files/{fid}/summary", headers={"X-Service-Token": "test-service"}).json()["status"] == "failed"
        assert len(model.calls) == 3
        claimed = c.post(f"/internal/jobs/{job}/claim", headers={"X-Service-Token": "test-service"}, json={"consumer": "test"})
        assert claimed.json()["state"] == "done"
        with SessionLocal() as db:
            assert db.get(OutboxMessage, job) is None
