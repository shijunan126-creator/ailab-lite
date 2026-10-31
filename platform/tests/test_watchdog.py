"""The progress watchdog replays the run's prefix, records a verdict, and in nudge mode warns once then pauses."""

import json

import pytest

from ailab_platform.agent import watchdog
from ailab_platform.config import settings
from ailab_platform.llm import LLMResponse, ToolCall
from tests.test_agent_loop import ScriptedLLM, _run

DRIFTING = LLMResponse(content='{"verdict":"drifting","reason":"在整理无关的文件"}')


def _write(name):
    return LLMResponse(tool_calls=[ToolCall(name, "write_file", {"path": f"/workspace/{name}.txt", "content": name})])


@pytest.fixture
def every_step(monkeypatch):
    monkeypatch.setattr(settings, "watchdog_interval_seconds", 0)


@pytest.mark.parametrize(
    "content,expected",
    [
        ('{"verdict":"stalled","reason":"重复失败"}', "stalled"),
        ('结论：{"verdict": "progressing", "reason": "已完成两页"}', "progressing"),
        ('{"verdict":"maybe"}', "unknown"),
        ("没有 JSON", "unknown"),
        (None, "unknown"),
    ],
)
def test_verdict_parsing(content, expected):
    assert watchdog.verdict(content)["verdict"] == expected


def test_observe_records_verdicts_without_touching_the_conversation(monkeypatch, every_step):
    monkeypatch.setattr(settings, "watchdog_mode", "observe")
    model = ScriptedLLM([_write("a"), DRIFTING, _write("b"), DRIFTING, LLMResponse(content="完成")])
    platform, _ = _run(model)
    assert [p["verdict"] for t, p in platform.events if t == "agent.watchdog"] == ["drifting", "drifting"]
    assert platform.statuses[-1][0] == "succeeded"
    check, following = model.seen_messages[1], model.seen_messages[2]
    assert check[-1] == {"role": "user", "content": watchdog.PROMPT}
    assert following == check[:-1]  # the check is a pure prefix of the next real request, and leaves nothing behind


def test_nudge_mode_warns_once_then_pauses_on_a_repeat(monkeypatch, every_step):
    monkeypatch.setattr(settings, "watchdog_mode", "nudge")
    model = ScriptedLLM([_write("a"), DRIFTING, _write("b"), DRIFTING])
    platform, _ = _run(model)
    nudged = model.seen_messages[2]
    assert nudged[-1]["content"] == watchdog.NUDGE.format(reason="在整理无关的文件")
    assert any(t == "agent.budget" and p["kind"] == "nudge" for t, p in platform.events)
    assert platform.paused["error"] == watchdog.PAUSE
    assert json.dumps(platform.checkpoint["state"])


def test_off_and_chat_never_call_the_judge(monkeypatch, every_step):
    monkeypatch.setattr(settings, "watchdog_mode", "off")
    model = ScriptedLLM([_write("a"), LLMResponse(content="完成")])
    platform, _ = _run(model)
    assert not [t for t, _ in platform.events if t == "agent.watchdog"] and len(model.seen_messages) == 2
