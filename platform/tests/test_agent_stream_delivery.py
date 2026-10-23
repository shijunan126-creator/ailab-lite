"""Visible text must not wait for the model to finish generating hidden tool arguments."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from ailab_platform.agent.loop import AgentLoop
from ailab_platform.catalog import Catalog, ModelSpec, load_catalog
from ailab_platform.config import Settings, settings
from ailab_platform.llm import LLMClient, LLMError, LLMResponse, OpenAICompatibleClient
from tests.test_agent_loop import FakeBroker, FakePlatform


def test_buffered_text_is_delivered_while_upstream_is_still_busy(monkeypatch):
    monkeypatch.setattr("ailab_platform.agent.loop.DELTA_FLUSH_SECONDS", 0.02)

    async def scenario():
        delivered = asyncio.Event()

        class Platform(FakePlatform):
            async def append_event(self, run_id, kind, payload=None):
                await super().append_event(run_id, kind, payload)
                text = "".join(p["text"] for t, p in self.events if t == "agent.delta")
                if text == "环境已就绪，开始编写脚本。":
                    delivered.set()

        class Streaming(LLMClient):
            async def chat_stream(self, *args, on_text=None, **kwargs):
                await on_text("环境")
                await on_text("已就绪，开始编写脚本。")
                # Simulate upstream building tool arguments, which produce no visible text.
                await asyncio.wait_for(delivered.wait(), timeout=1)
                return LLMResponse(content="环境已就绪，开始编写脚本。")

        platform = Platform()
        loop = AgentLoop(platform=platform, broker=FakeBroker())
        await loop._model_attempt(Streaming(), "run_1", {"_attempt_id": "attempt_1"}, "m", [], 0, tools=[])
        assert delivered.is_set()
        assert len(platform.settlements) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_stream_exit_flushes_partial_text_and_closes_upstream(cancel):
    async def scenario():
        entered = asyncio.Event()
        closed = asyncio.Event()

        class Streaming(LLMClient):
            async def chat_stream(self, *args, on_text=None, **kwargs):
                try:
                    await on_text("保留已经生成的文字")
                    entered.set()
                    if cancel:
                        await asyncio.Event().wait()
                    raise LLMError("upstream stopped")
                finally:
                    closed.set()

        platform = FakePlatform()
        loop = AgentLoop(platform=platform, broker=FakeBroker())
        task = asyncio.create_task(loop._model_attempt(Streaming(), "run_1", {"_attempt_id": "attempt_1"}, "m", [], 0, tools=[]))
        await asyncio.wait_for(entered.wait(), timeout=1)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else LLMError):
            await task
        assert closed.is_set()
        assert "".join(p["text"] for t, p in platform.events if t == "agent.delta") == "保留已经生成的文字"
        assert len(platform.settlements) == 1
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]

    asyncio.run(scenario())


def test_default_does_not_silently_cap_the_model_output():
    assert Settings.model_fields["model_max_output_tokens"].default is None


@pytest.mark.parametrize("ceiling,expected", [(None, 393216), (65536, 65536), (500000, 393216)])
def test_model_limit_controls_both_reservation_and_upstream(monkeypatch, ceiling, expected):
    catalog = Catalog([ModelSpec("large", "Large", provider="mock", context_tokens=1048576, max_output_tokens=393216)])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: catalog)
    monkeypatch.setattr(settings, "model_max_output_tokens", ceiling)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')

    async def scenario():
        platform = FakePlatform()
        loop = AgentLoop(platform=platform, broker=FakeBroker())
        llm = OpenAICompatibleClient("http://upstream", transport=httpx.MockTransport(respond))
        await loop._model_attempt(llm, "run_1", {"_attempt_id": "attempt_1"}, "large", [], 0, tools=[])
        assert next(iter(platform.reservations.values()))["max_output_tokens"] == expected
        assert requests[0]["max_tokens"] == expected

    asyncio.run(scenario())


def test_shipped_deepseek_catalog_declares_current_capacity():
    spec = load_catalog(Path(__file__).parents[1] / "models.toml").get("deepseek-v4.1-flash")
    assert spec.context_tokens == 1048576
    assert spec.max_output_tokens == 393216


def test_rejected_timer_flush_stops_upstream_and_settles_once(monkeypatch):
    from ailab_platform.clients import ClientError

    monkeypatch.setattr("ailab_platform.agent.loop.DELTA_FLUSH_SECONDS", 0.02)

    async def scenario():
        closed = asyncio.Event()

        class Platform(FakePlatform):
            async def append_event(self, run_id, kind, payload=None):
                if kind == "agent.delta":
                    raise ClientError("POST", "/events", 409, "stale attempt")
                await super().append_event(run_id, kind, payload)

        class Streaming(LLMClient):
            async def chat_stream(self, *args, on_text=None, **kwargs):
                try:
                    await on_text("等待刷新")
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        platform = Platform()
        loop = AgentLoop(platform=platform, broker=FakeBroker())
        with pytest.raises(ClientError, match="stale attempt"):
            await asyncio.wait_for(loop._model_attempt(Streaming(), "run_1", {"_attempt_id": "a"}, "m", [], 0, tools=[]), timeout=1)
        assert closed.is_set()
        assert len(platform.settlements) == 1
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]

    asyncio.run(scenario())
