"""Context boundaries are validated transactions, independent of event retention."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.context_protocol import protocol_groups, validate_safe_compaction_point
from ailab_platform.agent.context_rendering import (
    block,
    boundary_messages,
    loaded_modules,
    operational_context_message,
    source_handles,
    stored_summary_messages,
)
from ailab_platform.agent.loop import AgentLoop, BudgetPause
from ailab_platform.catalog import Catalog, ModelSpec
from ailab_platform.contracts.context_summary import ContextSummary, canonical_summary, validate_summary
from ailab_platform.llm import LLMError, LLMResponse
from tests.test_agent_loop import FakePlatform, ScriptedLLM


def pair(name="exec"):
    return [
        {"role": "assistant", "tool_calls": [{"id": "call", "function": {"name": name, "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call", "name": name, "content": '{"handle":"job_1","running":true}'},
    ]


@pytest.mark.parametrize(
    "messages,state",
    [
        (pair()[:1], {}),
        (pair()[1:], {}),
        (pair(), {"pending": [{"id": "other"}]}),
        (pair("run_subtasks")[:1], {}),
        (pair("run_subtasks"), {"pending": [{"name": "run_subtasks"}]}),
    ],
)
def test_unsafe_compaction_is_a_programming_error(messages, state):
    with pytest.raises(ValueError):
        validate_safe_compaction_point(messages, state)


def test_closed_background_and_subtasks_are_indivisible_safe_groups():
    assert validate_safe_compaction_point(pair(), {"background_jobs": ["job_1"]}) == [(0, 2)]
    assert protocol_groups(pair("run_subtasks")) == [(0, 2)]


def test_canonical_summary_byte_regression_and_source_validation():
    summary = ContextSummary.model_validate_json('{"objective":"中文","schema_version":1}')
    assert (
        canonical_summary(summary).encode()
        == (
            '{"schema_version":1,"objective":"中文","constraints":[],"decisions":[],"facts":[],"open_items":[],'
            '"next_actions":[],"uncertainties":[],"read_modules":[]}'
        ).encode()
    )
    text = json.dumps({"facts": [{"id": "f", "text": "fact", "sources": [{"run_id": "not_supplied"}]}]})
    with pytest.raises(ValueError, match="unknown summary source"):
        validate_summary(text, {"run_id": {"supplied"}})
    assert validate_summary(text, {"run_id": {"not_supplied"}}).facts
    with pytest.raises(ValueError):
        validate_summary('{"objective":"' + "中" * 16000 + '"}', {})
    assert not source_handles([{"role": "tool", "name": "recall_history", "content": '{"run_id":"recall"}'}])["run_id"]


def test_operational_and_module_blocks_are_deterministic_and_separate():
    state = {"background_jobs": ["b", "a"], "delivered": {"/b": 2, "/a": 1}, "workspace": {"key": "private"}, "plan": ["finish"]}
    reverse = {**state, "background_jobs": ["a", "b"], "delivered": {"/a": 1, "/b": 2}}
    assert operational_context_message(state) == operational_context_message(reverse)
    assert "private" not in operational_context_message(state)["content"]
    modules = [{"name": "platform.docx", "resource": "viewer/SKILL.md", "hash": "a" * 64}]
    messages = [block("loaded_modules", modules)]
    assert loaded_modules(messages) == modules
    blocks = boundary_messages(ContextSummary(objective="goal"), messages, state)
    assert "job_1" not in blocks[0]["content"]
    assert blocks[2] == messages[0]
    assert ContextSummary.model_validate_json(blocks[0]["content"].split("\n")[1]).objective == "goal"
    assert stored_summary_messages(canonical_summary(ContextSummary()))[0]["content"].startswith("<conversation_checkpoint>")


@pytest.mark.parametrize("scope", ["history", "parent", "work"])
@pytest.mark.parametrize("failure", ["invalid_json", "unknown_source"])
def test_bad_summary_does_not_advance_any_boundary(monkeypatch, scope, failure):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=10000, max_output_tokens=1000)
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    monkeypatch.setattr("ailab_platform.agent.loop.estimator.estimate", lambda model, value: 9100 if "OLDER" in json.dumps(value) else 100)

    class Platform(FakePlatform):
        async def get_model_context(self, *args, **kwargs):
            return {
                "summary": "",
                "through_run_id": None,
                "history": [{"id": "old", "prompt": "OLDER", "reply": "done"}],
                "has_more": False,
            }

        async def save_model_summary(self, *args, **kwargs):
            pytest.fail("invalid summary persisted")

    text = (
        "not json"
        if failure == "invalid_json"
        else json.dumps({"facts": [{"id": "f", "text": "fact", "sources": [{"file_id": "invented"}]}]})
    )
    loop = AgentLoop(platform=Platform(), llm=ScriptedLLM([LLMResponse(content=text)]))
    state = fresh_state()
    state.update(context_epoch=7, background_jobs=["job_1"], user_message_index=2)
    state["messages"] = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "OLDER"},
        *pair(),
    ]
    loop._states["r"] = state
    before = copy.deepcopy(state)
    run = {"_attempt_id": "a", "_fixed_catalog": []}
    with pytest.raises((BudgetPause, LLMError)):
        if scope == "history":
            asyncio.run(loop._assemble_context("r", run, "m", [*state["messages"][:2], state["messages"][2]]))
        elif scope == "parent":
            asyncio.run(loop._compact_parent_context("r", run, loop.llm, "m", state["messages"], {"role": "user", "content": "next"}, []))
        else:
            asyncio.run(loop._compact_work_context("r", SimpleNamespace(messages=state["messages"], run=run), loop.llm, "m", []))
    assert state == before
    assert not any(kind == "agent.context_boundary" for kind, _ in loop.platform.events)


def test_work_boundary_preserves_live_handle_and_rolls_back_failed_persistence(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=10000, max_output_tokens=1000)
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    monkeypatch.setattr("ailab_platform.agent.loop.estimator.estimate", lambda model, value: 9100 if "OLDER" in json.dumps(value) else 100)
    loop = AgentLoop(platform=FakePlatform(), llm=ScriptedLLM([LLMResponse(content=canonical_summary(ContextSummary()))] * 2))
    state = fresh_state()
    state.update(context_epoch=4, user_message_index=2, background_jobs=["job_1"])
    state["messages"] = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "OLDER"},
        *pair(),
    ]
    loop._states["r"] = state
    before = copy.deepcopy(state)
    turn = SimpleNamespace(messages=state["messages"], run={"_attempt_id": "a"})

    async def failed(*args):
        raise RuntimeError("storage failed")

    monkeypatch.setattr(loop, "_checkpoint", failed)
    with pytest.raises(RuntimeError, match="storage failed"):
        asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", []))
    assert state == before

    async def saved(*args):
        assert state["context_epoch"] == 5

    monkeypatch.setattr(loop, "_checkpoint", saved)
    asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", []))
    assert state["context_epoch"] == 5
    assert "job_1" in next(m["content"] for m in state["messages"] if "<runtime_state>" in str(m.get("content")))
    assert state["background_jobs"] == ["job_1"]


def test_one_valid_nonconverging_compaction_pauses_without_a_second_summary(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=10000, max_output_tokens=1000)
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    monkeypatch.setattr(
        "ailab_platform.agent.loop.estimator.estimate", lambda model, value: 9150 if "protected" in json.dumps(value) else 100
    )
    model = ScriptedLLM([LLMResponse(content=canonical_summary(ContextSummary(objective="goal")))])
    loop = AgentLoop(platform=FakePlatform(), llm=model)
    state = fresh_state()
    state.update(context_epoch=2, user_message_index=2)
    state["messages"] = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "protected"},
        {"role": "assistant", "content": "old"},
    ]
    loop._states["r"] = state

    async def saved(*args):
        pass

    monkeypatch.setattr(loop, "_checkpoint", saved)
    turn = SimpleNamespace(messages=state["messages"], run={"_attempt_id": "a"})
    for _ in range(2):
        with pytest.raises(BudgetPause):
            asyncio.run(loop._compact_work_context("r", turn, model, "m", []))
    assert state["context_epoch"] == 3
    assert len(model.seen_messages) == 1
