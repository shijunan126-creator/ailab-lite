"""Usage transport, stream and cancellation faults retain durable accounting."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from ailab_platform.agent.loop import AgentLoop
from ailab_platform.clients import AsyncPlatformClient
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.llm import LLMResponse, OpenAICompatibleClient
from ailab_platform.models import ModelUsage, UsageReservation
from ailab_platform.quota import expire_reservations
from tests.test_agent_loop import FakePlatform, RichBroker, ScriptedLLM, _run
from tests.test_ledger_protocol import AUTH, api  # noqa: F401


def test_settle_api_outage_is_reaped_and_late_delivery_corrects_the_same_call(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr("ailab_platform.clients.USAGE_RETRY_BACKOFF_SECONDS", 0)
    run_id = api.post("/v1/runs", headers=AUTH, json={"prompt": "hi", "model": "test-fast"}).json()["id"]
    submitted = []

    async def scenario():
        platform = AsyncPlatformClient(base_url="http://testserver", token="test-service")
        await platform._http.aclose()
        from ailab_platform.api.main import app

        platform._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        original = platform._send

        async def failing(req):
            if req.url.endswith("/usage/settle"):
                submitted.append(dict(req.kwargs["json"]))
                raise httpx.ConnectError("API unavailable")
            return await original(req)

        monkeypatch.setattr(platform, "_send", failing)
        try:
            llm = ScriptedLLM([LLMResponse(content="answer", usage={"prompt_tokens": 20, "completion_tokens": 5})])
            await AgentLoop(platform=platform, broker=RichBroker(), llm=llm).process(run_id)
            assert len(submitted) == 4 and all(body == submitted[0] for body in submitted)
            with SessionLocal() as db:
                reservation = db.get(UsageReservation, submitted[0]["call_id"])
                assert reservation.status == "open"
                expire_reservations(db, datetime.now(UTC) + timedelta(seconds=settings.usage_reservation_ttl_seconds + 1))
                row = db.scalar(select(ModelUsage).where(ModelUsage.call_id == reservation.call_id))
                assert row.status == "unreconciled" and row.weighted_amount == reservation.reserved_amount
                row_id = row.id
            monkeypatch.setattr(platform, "_send", original)
            result = await platform.settle_usage(submitted[0])
            assert result["status"] == "settled" and result["weighted_amount"] == 25000
            assert await platform.settle_usage(submitted[0]) == result
            with SessionLocal() as db:
                assert db.scalar(select(ModelUsage.id).where(ModelUsage.call_id == result["call_id"])) == row_id
        finally:
            await platform.aclose()

    asyncio.run(scenario())


def test_broken_stream_is_estimated_and_ambiguous_inference_is_never_retried():
    seen = []

    def handler(req):
        seen.append(req)
        chunk = {"model": "real-backend-42", "choices": [{"delta": {"content": "partial output"}}]}
        return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\n")

    llm = OpenAICompatibleClient("http://gateway/v1", transport=httpx.MockTransport(handler))
    platform, _ = _run(llm)
    assert len(seen) == 1
    body = json.loads(seen[0].content)
    assert body["max_tokens"] == settings.model_max_output_tokens
    settled = next(iter(platform.settlements.values()))
    assert settled["status"] == "estimated" and settled["output_tokens"] > 0
    assert settled["actual_backend"] == "real-backend-42"
    assert seen[0].headers["x-call-id"] == settled["call_id"] and seen[0].headers["x-run-id"] == "run_1"
    assert platform.statuses[-1][0] == "failed"


def test_failure_before_connect_releases_instead_of_inventing_usage():
    def handler(req):
        raise httpx.ConnectError("not connected", request=req)

    platform, _ = _run(OpenAICompatibleClient("http://gateway/v1", transport=httpx.MockTransport(handler)))
    settled = next(iter(platform.settlements.values()))
    assert settled["status"] == "released"
    assert settled["input_tokens"] == settled["output_tokens"] == 0


def test_cancel_between_reserve_and_dispatch_releases_the_intent():
    class CancelAfterReserve(FakePlatform):
        async def get_run_state(self, run_id):
            return {"status": "canceled" if self.reservations else "running"}

    llm = ScriptedLLM([])
    platform, _ = _run(llm, platform=CancelAfterReserve())
    assert llm.seen_messages == []
    assert next(iter(platform.settlements.values()))["status"] == "released"


def test_cancel_during_stream_settles_partial_consumption_and_closes_it(monkeypatch):
    monkeypatch.setattr("ailab_platform.agent.loop.CANCEL_POLL_SECONDS", 0.01)
    closed = []

    class Streaming(ScriptedLLM):
        async def chat(self, *args, **kwargs):
            try:
                await asyncio.sleep(30)
            finally:
                closed.append(True)

    class CancelDuringCall(FakePlatform):
        polls = 0

        async def get_run_state(self, run_id):
            self.polls += 1
            return {"status": "canceled" if self.polls > 2 else "running"}

    platform, _ = _run(Streaming([]), platform=CancelDuringCall())
    assert closed == [True]
    assert next(iter(platform.settlements.values()))["status"] == "estimated"


def test_rerun_has_new_call_ids_and_attempt_ids():
    platform = FakePlatform()
    _run(ScriptedLLM([LLMResponse(content="first", usage={"prompt_tokens": 2})]), platform=platform)
    _run(ScriptedLLM([LLMResponse(content="second", usage={"prompt_tokens": 3})]), platform=platform)
    assert len(platform.settlements) == 2
    assert len({r["attempt_id"] for r in platform.reservations.values()}) == 2


def test_stream_with_usage_preserves_reasoning_and_actual_backend():
    def handler(req):
        chunk = {
            "model": "backend-7",
            "choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "completion_tokens_details": {"reasoning_tokens": 3}},
        }
        return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")

    platform, _ = _run(OpenAICompatibleClient("http://gateway/v1", transport=httpx.MockTransport(handler)))
    row = next(iter(platform.settlements.values()))
    assert row["status"] == "settled" and row["reasoning_tokens"] == 3 and row["output_tokens"] == 5
    assert row["actual_backend"] == "backend-7"


def test_gateway_timeout_does_not_release_ambiguous_model_consumption():
    for status in (408, 502, 503):
        platform, _ = _run(
            OpenAICompatibleClient(
                "http://gateway/v1", transport=httpx.MockTransport(lambda req: httpx.Response(status, text="upstream failed"))
            )
        )
        row = next(iter(platform.settlements.values()))
        assert row["status"] == "estimated" and row["input_tokens"] > 0
