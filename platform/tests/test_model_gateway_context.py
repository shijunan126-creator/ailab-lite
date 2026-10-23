"""T14 acceptance against controllable TCP upstreams and the real ledger API."""

import asyncio
import json
from contextlib import asynccontextmanager, suppress

import pytest
from fastapi.testclient import TestClient

from ailab_platform import catalog as catalog_mod
from ailab_platform.agent.loop import AgentLoop
from ailab_platform.api.main import app
from ailab_platform.catalog import Catalog, ModelSpec
from ailab_platform.config import settings
from ailab_platform.llm import LLMError, LLMResponse, OpenAICompatibleClient, reset_llm_cache
from tests.run_claims import attempt_headers

AUTH = {"Authorization": "Bearer test-token"}
SERVICE = {"X-Service-Token": "test-service"}


@asynccontextmanager
async def upstream(kind, seen):
    tasks = set()

    async def respond(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = int(next(x.split(b":", 1)[1] for x in head.split(b"\r\n") if x.lower().startswith(b"content-length:")))
            seen.append((head, json.loads(await reader.readexactly(length))))
            if kind == "429":
                writer.write(b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 4\r\n\r\nbusy")
                await writer.drain()
                return
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
            await writer.drain()
            if kind == "slow":
                await reader.read()
                return
            delta = (
                {"tool_calls": [{"index": 0, "id": "tc", "function": {"name": "exec", "arguments": "{"}}]}
                if kind == "tool-broken"
                else {"content": "城市更新汇报" if kind == "title" else "hello"}
            )
            chunk = {"model": "actual-" + kind, "choices": [{"delta": delta}]}
            writer.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            if kind in {"ok", "title"}:
                writer.write(
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
                    b'"usage":{"prompt_tokens":10,"completion_tokens":2}}\n\ndata: [DONE]\n\n'
                )
            await writer.drain()
            if kind == "idle":
                await reader.read()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        await server.wait_closed()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class LedgerBridge:
    def __init__(self, client):
        self.client = client
        self.events = []

    async def get_run_state(self, run_id):
        return {"status": "running"}

    async def reserve_usage(self, payload):
        r = self.client.post("/internal/usage/reserve", headers=SERVICE, json=payload)
        assert r.status_code == 200, r.text
        return r.json()

    async def settle_usage(self, payload):
        r = self.client.post("/internal/usage/settle", headers=SERVICE, json=payload)
        assert r.status_code == 200, r.text
        return r.json()

    async def append_event(self, run_id, type_, payload=None):
        self.events.append((type_, payload))


def install_catalog(monkeypatch, first, second):
    catalog = Catalog(
        [
            ModelSpec("primary", "Primary", base_url=first, weight=3),
            ModelSpec("backup", "Backup", base_url=second, weight=1),
        ]
    )
    object.__setattr__(catalog.models["primary"], "fallback", ["backup"])
    monkeypatch.setattr(catalog_mod, "get_catalog", lambda: catalog)
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: catalog)
    monkeypatch.setattr("ailab_platform.llm.get_catalog", lambda: catalog)
    monkeypatch.setattr("ailab_platform.quota.get_catalog", lambda: catalog)
    monkeypatch.setattr("ailab_platform.api.routes_runs.get_catalog", lambda: catalog)
    reset_llm_cache()
    return catalog


@pytest.mark.parametrize("kind", ["429", "slow"])
def test_pre_output_fallback_has_distinct_calls_and_real_ledger(monkeypatch, kind):
    from types import SimpleNamespace

    ttft = []
    monkeypatch.setattr(
        "ailab_platform.agent.loop.metrics.MODEL_TTFT_SECONDS",
        SimpleNamespace(labels=lambda label: SimpleNamespace(observe=lambda value: ttft.append((label, value)))),
        raising=False,
    )

    async def exercise(c):
        seen1, seen2 = [], []
        async with upstream(kind, seen1) as first, upstream("ok", seen2) as second:
            install_catalog(monkeypatch, first, second)
            run = c.post("/v1/runs", headers=AUTH, json={"prompt": "hi", "model": "primary", "mode": "chat"}).json()
            assert "id" in run, run
            owner = attempt_headers(c, run["id"])
            assert c.post(f"/internal/runs/{run['id']}/status", headers=owner, json={"status": "running"}).status_code == 200
            bridge = LedgerBridge(c)
            loop = AgentLoop(platform=bridge)
            result = await loop._call_model(
                OpenAICompatibleClient(first),
                run["id"],
                {**run, "_attempt_id": owner["X-Run-Attempt"]},
                "primary",
                [{"role": "user", "content": "hi"}],
                0,
                tools=[],
            )
            assert result.content == "hello"
            from sqlalchemy import select

            from ailab_platform.db import SessionLocal
            from ailab_platform.models import ModelUsage

            with SessionLocal() as db:
                rows = list(db.scalars(select(ModelUsage).where(ModelUsage.run_id == run["id"]).order_by(ModelUsage.created_at)))
                assert len(rows) == 2
                assert len({r.call_id for r in rows}) == 2
                assert [r.alias for r in rows] == ["primary", "backup"]
                assert rows[1].actual_backend == "actual-ok"
                assert float(rows[0].weight) == 3 and float(rows[1].weight) == 1
            assert any(t == "agent.model_fallback" for t, _ in bridge.events)
            assert len(seen1) == len(seen2) == 1
            assert len(ttft) == 1 and ttft[0][0] == "backup" and ttft[0][1] > 0

    monkeypatch.setattr(settings, "ttft_timeout_seconds", 0.5)
    with TestClient(app) as c:
        asyncio.run(exercise(c))


@pytest.mark.parametrize("kind", ["broken", "tool-broken", "idle"])
def test_output_never_replayed(monkeypatch, kind):
    from tests.test_agent_loop import FakePlatform

    async def exercise():
        one, two = [], []
        async with upstream(kind, one) as first, upstream("ok", two) as second:
            install_catalog(monkeypatch, first, second)
            bridge = FakePlatform()
            loop = AgentLoop(platform=bridge)
            with pytest.raises(LLMError):
                await loop._call_model(
                    OpenAICompatibleClient(first),
                    "run",
                    {"_attempt_id": "attempt"},
                    "primary",
                    [{"role": "user", "content": "hi"}],
                    0,
                    tools=[],
                )
            assert len(one) == 1 and not two
            assert len(bridge.reservations) == len(bridge.settlements) == 1

    monkeypatch.setattr(settings, "llm_idle_timeout_seconds", 0.5)
    asyncio.run(exercise())


def test_all_failed_and_capability_filter(monkeypatch):
    from ailab_platform.model_policy import ModelPolicy
    from tests.test_agent_loop import FakePlatform

    async def exercise():
        one, two = [], []
        async with upstream("429", one) as first, upstream("429", two) as second:
            catalog = install_catalog(monkeypatch, first, second)
            catalog.models["backup"] = ModelSpec("backup", "Backup", base_url=second, capabilities=[])
            assert [s.id for s in ModelPolicy(catalog).candidates("primary", {"tools"})] == ["primary"]
            bridge = FakePlatform()
            with pytest.raises(LLMError, match="429"):
                await AgentLoop(platform=bridge)._call_model(
                    OpenAICompatibleClient(first),
                    "run",
                    {"_attempt_id": "attempt"},
                    "primary",
                    [{"role": "user", "content": "hi"}],
                    0,
                    tools=[],
                )
            assert len(bridge.reservations) == 2
            assert len(bridge.settlements) == 2

    monkeypatch.setattr(settings, "llm_max_retries", 0)
    asyncio.run(exercise())


def test_200_round_budget_and_incremental_summary(monkeypatch):
    from ailab_platform.llm import LLMClient
    from ailab_platform.model_policy import ModelPolicy, estimator
    from tests.test_agent_loop import FakePlatform

    class SummaryLLM(LLMClient):
        async def chat(self, messages, tools, model, user_id=None):
            assert estimator.estimate(model, {"messages": messages, "tools": tools}) <= 2000
            return LLMResponse(content="记住用户要求与已交付文件。", usage={"prompt_tokens": 50, "completion_tokens": 15})

    class Context(FakePlatform):
        def __init__(self):
            super().__init__()
            self.summary = ""
            self.through = None
            self.saved = []
            self.history = [{"id": str(n), "prompt": "要求" * 100, "reply": "完成" * 100} for n in range(200)]

        async def get_model_context(self, run_id, after=None):
            start = int(after or self.through or -1) + 1
            return {
                "summary": self.summary,
                "through_run_id": self.through,
                "history": self.history[start : start + 100],
                "has_more": len(self.history) > start + 100,
            }

        async def save_model_summary(self, run_id, payload):
            assert payload["expected_through_run_id"] == self.through
            self.summary, self.through = payload["summary"], payload["through_run_id"]
            self.saved.append(payload)

    cat = Catalog([ModelSpec("summary", "Summary", provider="mock", context_tokens=3000, max_output_tokens=1000)])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: cat)
    bridge = Context()

    async def exercise():
        loop = AgentLoop(platform=bridge, llm=SummaryLLM())
        base = [{"role": "system", "content": "系统"}, {"role": "user", "content": "当前问题"}]
        messages = await loop._assemble_context("run", {"_attempt_id": "attempt"}, "summary", base)
        assert estimator.estimate("summary", {"messages": messages, "tools": []}) <= ModelPolicy.input_budget(cat.get("summary"))
        assert any("历史摘要" in m["content"] for m in messages)
        calls = len(bridge.reservations)
        assert calls > 0 and calls == len(bridge.settlements) == len(bridge.saved)
        again = await loop._assemble_context("run", {"_attempt_id": "attempt"}, "summary", base)
        assert again == messages
        assert len(bridge.reservations) == calls

    asyncio.run(exercise())


def test_first_output_deadline_on_real_slow_socket(monkeypatch):
    async def exercise():
        seen = []
        async with upstream("slow", seen) as address:
            with pytest.raises(LLMError, match="deadline"):
                await asyncio.wait_for(OpenAICompatibleClient(address).chat_stream([{"role": "user", "content": "hi"}], [], "primary"), 2.0)
            assert len(seen) == 1

    from types import SimpleNamespace

    if hasattr(settings, "ttft_timeout_seconds"):
        monkeypatch.setattr(settings, "ttft_timeout_seconds", 0.5)
    else:
        monkeypatch.setattr("ailab_platform.llm.settings", SimpleNamespace(**{**settings.model_dump(), "ttft_timeout_seconds": 0.5}))
    asyncio.run(exercise())


def test_context_api_keyset_and_summary_compare_and_set():
    from datetime import timedelta

    from ailab_platform.db import SessionLocal
    from ailab_platform.models import Run

    with TestClient(app) as c:
        conversation = c.post("/v1/conversations", headers=AUTH, json={}).json()
        current = c.post(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH, json={"prompt": "latest"}).json()
        with SessionLocal() as db:
            run = db.get(Run, current["id"])
            for n in range(200):
                db.add(
                    Run(
                        id=f"ctx-{run.id}-{n:03}",
                        conversation_id=run.conversation_id,
                        user_id=run.user_id,
                        prompt=f"history-{n}",
                        summary="answer",
                        status="succeeded",
                        model=run.model,
                        created_at=run.created_at - timedelta(seconds=201 - n),
                    )
                )
            db.commit()
        bootstrap = c.get(f"/internal/runs/{current['id']}/model-input", headers=SERVICE).json()
        assert bootstrap["prompt"] == "latest" and bootstrap["history"] == []
        assert bootstrap["needs_conversation_title"] is True
        assert bootstrap["workspace_restore_key"] is None
        url = f"/internal/runs/{current['id']}/model-context"
        first = c.get(url, headers=SERVICE).json()
        assert len(first["history"]) == 100 and first["has_more"]
        second = c.get(url, headers=SERVICE, params={"after": first["history"][-1]["id"]}).json()
        assert len(second["history"]) == 100 and not second["has_more"]
        assert first["history"][0]["prompt"] == "history-0"
        summary_url = f"/internal/runs/{current['id']}/model-summary"
        owner = attempt_headers(c, current["id"])
        body = {"expected_through_run_id": None, "through_run_id": first["history"][-1]["id"], "summary": "important decisions"}
        assert c.put(summary_url, headers=owner, json=body).status_code == 200
        assert c.put(summary_url, headers=owner, json=body).status_code == 409
        next_page = c.get(url, headers=SERVICE).json()
        assert next_page["summary"] == "important decisions"
        assert next_page["history"] == second["history"]
        body.update(expected_through_run_id=body["through_run_id"], through_run_id=current["id"])
        assert c.put(summary_url, headers=owner, json=body).status_code == 409
        assert c.get(url, headers=AUTH).status_code == 403


@pytest.mark.parametrize("title_result", ["城市更新汇报", "", "error", "quota", "settlement"])
def test_optional_title_keeps_successful_answer_and_plain_text(monkeypatch, title_result):
    from ailab_platform.llm import LLMClient
    from tests.test_agent_loop import FakeBroker, FakePlatform

    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "needs_conversation_title": True, "options": {"mode": "chat"}}

        async def reserve_usage(self, payload):
            if title_result == "quota" and self.reservations:
                raise RuntimeError("quota exceeded")
            return await super().reserve_usage(payload)

        async def settle_usage(self, payload):
            result = await super().settle_usage(payload)
            if title_result == "settlement" and len(self.settlements) == 2:
                raise RuntimeError("settlement retries exhausted")
            return result

    class Model(LLMClient):
        count = 0

        async def chat(self, messages, tools, model, user_id=None):
            self.count += 1
            if self.count == 1:
                return LLMResponse(content="这里是确认后的回答。", usage={"prompt_tokens": 5, "completion_tokens": 3})
            if title_result == "error":
                raise LLMError("title unavailable")
            return LLMResponse(content=title_result, usage={"prompt_tokens": 4, "completion_tokens": 2})

    platform = Platform()
    asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=Model()).process("run_1", attempt_id="att_1"))
    status, result = platform.statuses[-1]
    assert status == "succeeded" and result["summary"] == "这里是确认后的回答。"
    assert result["result"].get("conversation_title") == (title_result if title_result == "城市更新汇报" else None)
    assert len(platform.reservations) == (1 if title_result == "quota" else 2)
    assert len(platform.reservations) == len(platform.settlements)
    assert any(p["max_output_tokens"] == 64 for p in platform.reservations.values()) if title_result != "quota" else True
    assert all(payload["step"] != -2 for name, payload in platform.events if name in {"agent.delta", "agent.thinking"})


def test_title_uses_real_ledger_and_bounded_model_request(monkeypatch):
    from sqlalchemy import select

    from ailab_platform.db import SessionLocal
    from ailab_platform.models import ModelUsage

    async def exercise(c):
        seen = []
        async with upstream("title", seen) as address:
            install_catalog(monkeypatch, address, address)
            run = c.post("/v1/runs", headers=AUTH, json={"prompt": "城市更新的汇报怎么组织", "model": "primary", "mode": "chat"}).json()
            owner = attempt_headers(c, run["id"])
            assert c.post(f"/internal/runs/{run['id']}/status", headers=owner, json={"status": "running"}).status_code == 200
            loop = AgentLoop(platform=LedgerBridge(c))
            run.update(_attempt_id=owner["X-Run-Attempt"], needs_conversation_title=True)
            response = await loop._call_model(
                OpenAICompatibleClient(address), run["id"], run, "primary", [{"role": "user", "content": run["prompt"]}], 0, tools=[]
            )
            run["prompt"] *= 500
            title = await loop._conversation_title(run["id"], run, response.content * 500)
            assert title == "城市更新汇报"
            assert len(seen) == 2 and seen[1][1]["max_tokens"] == 64
            title_input = json.loads(seen[1][1]["messages"][1]["content"])
            assert len(title_input["question"]) == len(title_input["answer"]) == 1000
            with SessionLocal() as db:
                rows = list(db.scalars(select(ModelUsage).where(ModelUsage.run_id == run["id"])))
                assert len(rows) == 2 and len({r.call_id for r in rows}) == 2
                assert all(r.alias == "primary" and r.actual_backend == "actual-title" for r in rows)

    with TestClient(app) as c:
        asyncio.run(exercise(c))


def test_settlement_failure_stops_fallback(monkeypatch):
    from tests.test_agent_loop import FakePlatform

    class BrokenLedger(FakePlatform):
        async def settle_usage(self, payload):
            raise RuntimeError("ledger unavailable")

    async def exercise():
        first_seen, second_seen = [], []
        async with upstream("429", first_seen) as first, upstream("ok", second_seen) as second:
            install_catalog(monkeypatch, first, second)
            with pytest.raises(RuntimeError, match="settlement failed"):
                await AgentLoop(platform=BrokenLedger())._call_model(
                    OpenAICompatibleClient(first),
                    "run",
                    {"_attempt_id": "attempt"},
                    "primary",
                    [{"role": "user", "content": "hi"}],
                    0,
                    tools=[],
                )
            assert len(first_seen) == 1 and not second_seen

    asyncio.run(exercise())
