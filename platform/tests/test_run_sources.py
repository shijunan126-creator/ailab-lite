"""Turn sources are bounded durable output, independent of the SSE history."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from ailab_platform.adapters.runtime import fresh_state, restored_state
from ailab_platform.agent.loop import AgentLoop
from ailab_platform.api.main import app
from ailab_platform.clients import ClientError
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.llm import LLMResponse, ToolCall
from ailab_platform.models import Run
from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM
from tests.test_conversations import AUTH, _finish


def source(index, title=None):
    return {"title": title or f"Page {index}", "url": f"https://example.com/{index}", "site": "example.com"}


def search(index):
    return LLMResponse(tool_calls=[ToolCall(f"s{index}", "web_search", {"query": str(index)})])


@pytest.fixture(autouse=True)
def enable_search(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "mock")


@pytest.fixture
def web_tools(monkeypatch):
    async def execute(name, arguments, *args):
        if name == "web_search":
            index = int(arguments["query"])
            return {"ok": True, "results": [source(index), source(index + 1), {"url": "file:///etc/passwd"}]}
        assert name == "read_url"
        return {"ok": True, **source(1, "Later title")}

    monkeypatch.setattr("ailab_platform.agent.loop.execute_tool", execute)


def test_sources_from_search_and_read_are_deduplicated_in_first_seen_order(web_tools):
    platform = FakePlatform()
    model = ScriptedLLM(
        [
            search(0),
            LLMResponse(tool_calls=[ToolCall("read", "read_url", {"url": source(1)["url"]})]),
            search(1),
            LLMResponse(content="done"),
        ]
    )
    asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=model).process("r", attempt_id="a"))
    expected = [source(i) for i in range(3)]
    assert platform.statuses[-1][0] == "succeeded"
    assert platform.statuses[-1][1]["result"]["sources"] == expected
    assert platform.checkpoint["state"]["sources"] == expected


def test_turn_sources_cap_is_shared_across_tool_calls(monkeypatch):
    monkeypatch.setattr(settings, "max_agent_steps", 20)

    async def execute(name, arguments, *args):
        start = int(arguments["query"]) * 10
        return {"ok": True, "results": [source(i) for i in range(start, start + 10)]}

    monkeypatch.setattr("ailab_platform.agent.loop.execute_tool", execute)
    platform = FakePlatform()
    model = ScriptedLLM([*[search(i) for i in range(6)], LLMResponse(content="done")])
    asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=model).process("r", attempt_id="a"))
    assert platform.statuses[-1][1]["result"]["sources"] == [source(i) for i in range(50)]
    assert platform.checkpoint["state"]["sources"] == [source(i) for i in range(50)]


def test_sources_survive_checkpoint_and_waiting_user_resume(web_tools):
    platform = FakePlatform()
    ask = ToolCall("ask", "ask_user", {"question": "Continue?"})
    asyncio.run(
        AgentLoop(platform=platform, broker=FakeBroker(), llm=ScriptedLLM([search(0), LLMResponse(tool_calls=[ask])])).process(
            "r", attempt_id="a"
        )
    )
    assert platform.paused["status"] == "waiting_user"
    assert platform.checkpoint["state"]["sources"] == [source(0), source(1)]
    platform.checkpoint["answer"] = {"question_id": platform.paused["question"]["question_id"], "answer": "yes"}
    asyncio.run(
        AgentLoop(platform=platform, broker=FakeBroker(), llm=ScriptedLLM([search(1), LLMResponse(content="done")])).resume(
            "r", platform.checkpoint, attempt_id="b", attempt=2
        )
    )
    assert platform.statuses[-1][0] == "succeeded"
    assert platform.statuses[-1][1]["result"]["sources"] == [source(i) for i in range(3)]


def test_legacy_checkpoint_defaults_to_empty_sources():
    state = fresh_state()
    state.pop("sources")
    assert restored_state({"state": state})["sources"] == []


@pytest.mark.parametrize("failure", ["budget", "client", "unexpected"])
def test_failed_run_keeps_completed_sources(web_tools, failure):
    class Failing(ScriptedLLM):
        async def chat(self, *args, **kwargs):
            if not self.responses:
                if failure == "client":
                    raise ClientError("GET", "/test", 503, "unavailable")
                raise RuntimeError("unexpected failure")
            return await super().chat(*args, **kwargs)

    platform = FakePlatform()
    # The first call completes its tool, then the next call exhausts the budget or fails.
    responses = [search(0)]
    if failure == "budget":
        responses.append(
            LLMResponse(tool_calls=[ToolCall("next", "web_search", {"query": "1"})], usage={"completion_tokens": settings.run_max_tokens})
        )
    asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=Failing(responses)).process("r", attempt_id="a"))
    assert platform.statuses[-1][0] == "failed"
    assert platform.statuses[-1][1]["result"]["sources"] == [source(0), source(1)]


def test_conversation_and_single_run_return_sources_without_events():
    with TestClient(app) as client:
        conversation = client.post("/v1/conversations", headers=AUTH, json={}).json()
        run = client.post(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH, json={"prompt": "Search"}).json()
        _finish(client, run["id"], "done")
        with SessionLocal() as db:
            db.get(Run, run["id"]).result = {"sources": [source(0)], "workspace_key": "private"}
            db.commit()
        detail = client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH).json()
        assert detail["turns"][0]["result"]["sources"] == [source(0)]
        assert "workspace_key" not in detail["turns"][0]["result"]
        assert client.get(f"/v1/runs/{run['id']}", headers=AUTH).json()["result"]["sources"] == [source(0)]
