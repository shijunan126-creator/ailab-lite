"""Parallel web tool batches and durable web reads across an interruption."""

import asyncio
import json
import time

from ailab_platform import web
from ailab_platform.agent.loop import AgentLoop, RunCanceled
from ailab_platform.config import settings
from ailab_platform.llm import LLMResponse, ToolCall
from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM

A = "https://example.com/a"
B = "https://example.com/b"


def _read(call_id, url, start=0):
    return ToolCall(call_id, "read_url", {"url": url, **({"start": start} if start else {})})


def _result(url):
    return {"ok": True, "url": url, "title": url, "text": "page", "links": [], "truncated": False}


def test_two_web_reads_in_one_reply_run_at_the_same_time(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "mock")
    calls = [_read("a", A), _read("b", B)]

    async def execute(name, arguments, *args):
        assert name == "read_url"
        await asyncio.sleep(0.3)
        return _result(arguments["url"])

    monkeypatch.setattr("ailab_platform.agent.loop.execute_tool", execute)
    llm = ScriptedLLM([LLMResponse(tool_calls=calls), LLMResponse(content="done")])
    platform = FakePlatform()
    started = time.monotonic()
    asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=llm).process("run_1", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert time.monotonic() - started < 0.5  # two 0.3s reads, not 0.6s
    tool_messages = [message for message in llm.seen_messages[1] if message["role"] == "tool"]
    assert [json.loads(message["content"])["url"] for message in tool_messages] == [A, B]


def test_an_interrupted_batch_keeps_the_completed_web_read(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "mock")

    async def first_attempt(name, arguments, *args):
        if arguments["url"] == B:
            raise RunCanceled()
        return _result(arguments["url"])

    monkeypatch.setattr("ailab_platform.agent.loop.execute_tool", first_attempt)
    platform = FakePlatform()
    llm = ScriptedLLM([LLMResponse(tool_calls=[_read("a", A), _read("b", B)])])
    asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=llm).process("run_1", attempt_id="a"))
    assert ("agent.canceled", {}) in platform.events
    records = platform.checkpoint["state"]["tool_log"]
    assert any(record.get("result", {}).get("url") == A for record in records.values())

    executed = []

    async def resumed(name, arguments, *args):
        executed.append(arguments["url"])
        return _result(arguments["url"])

    monkeypatch.setattr("ailab_platform.agent.loop.execute_tool", resumed)
    llm = ScriptedLLM([LLMResponse(content="done")])
    asyncio.run(
        AgentLoop(platform=platform, broker=FakeBroker(), llm=llm).process(
            "run_1", attempt=2, attempt_id="b", checkpoint=platform.checkpoint
        )
    )
    assert platform.statuses[-1][0] == "succeeded"
    assert executed == [B]  # the completed read was reused, not fetched again
    tool_messages = [message for message in llm.seen_messages[0] if message["role"] == "tool"]
    assert [json.loads(message["content"])["url"] for message in tool_messages] == [A, B]


def test_a_read_is_not_fetched_again_after_a_checkpoint_restore(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "mock")
    reads = []

    async def read(url, max_chars=None, start=0):
        reads.append((url, start))
        return {**_result(url), "next_start": 4000}

    monkeypatch.setattr(web, "read_url", read)
    url = "https://github.com/example/repo"
    ask = ToolCall("ask", "ask_user", {"question": "继续?"})
    platform = FakePlatform()
    llm = ScriptedLLM([LLMResponse(tool_calls=[_read("a", url)]), LLMResponse(tool_calls=[ask])])
    asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=llm).process("run_1", attempt_id="a"))
    assert platform.paused["status"] == "waiting_user"
    assert reads == [(url, 0)]
    assert platform.checkpoint["state"]["web_reads"]
    platform.checkpoint["answer"] = {"question_id": platform.paused["question"]["question_id"], "answer": "yes"}

    llm = ScriptedLLM([LLMResponse(tool_calls=[_read("a", url)]), LLMResponse(content="done")])
    asyncio.run(
        AgentLoop(platform=platform, broker=FakeBroker(), llm=llm).process(
            "run_1", attempt=2, attempt_id="b", checkpoint=platform.checkpoint
        )
    )
    assert platform.statuses[-1][0] == "succeeded"
    assert reads == [(url, 0)]  # restored from the checkpoint, no second fetch
    duplicate = json.loads([message for message in llm.seen_messages[1] if message["role"] == "tool"][-1]["content"])
    assert duplicate["duplicate"] is True
    assert "已读过这一段" in duplicate["note"] and "start=4000" in duplicate["note"]
