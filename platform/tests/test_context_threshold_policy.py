"""Full-window thresholds and per-call output allocation; no real-model capacity claim."""

import asyncio
import json

import pytest

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.loop import AgentLoop, _Turn
from ailab_platform.catalog import Catalog, ModelSpec
from ailab_platform.llm import LLMResponse
from ailab_platform.model_policy import ModelPolicy
from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM


@pytest.mark.parametrize("context,expected", [(10000, 8500), (20000, 17000), (1048576, 891289)])
def test_threshold_uses_full_model_window_independent_of_maximum_output(context, expected):
    spec = ModelSpec("m", "m", context_tokens=context, max_output_tokens=context // 2)
    assert ModelPolicy.compaction_threshold(spec) == expected
    assert ModelPolicy.input_budget(spec) == context - 1
    assert ModelPolicy.output_budget(spec, context * 8 // 10) == context - context * 8 // 10
    assert ModelPolicy.output_budget(spec, context) == 0


@pytest.mark.parametrize("mode", ["chat", "work"])
@pytest.mark.parametrize("context,used,compacts", [(10000, 8499, False), (10000, 8500, True), (20000, 8500, False)])
def test_history_threshold_boundary_and_model_change(monkeypatch, mode, context, used, compacts):
    cat = Catalog([ModelSpec("m", "m", provider="mock", context_tokens=context, max_output_tokens=1000)])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: cat)
    monkeypatch.setattr("ailab_platform.agent.loop.estimator.estimate", lambda model, value: used if "OLDER" in json.dumps(value) else 100)

    class Context(FakePlatform):
        async def get_model_context(self, *args, **kwargs):
            return {
                "summary": "",
                "through_run_id": None,
                "history": [{"id": "old", "prompt": "OLDER", "reply": "done"}],
                "has_more": False,
            }

        async def save_model_summary(self, id_, payload):
            self.saved.append(payload)

    async def exercise():
        platform = Context()
        platform.saved = []
        loop = AgentLoop(platform=platform, llm=ScriptedLLM([LLMResponse(content="summary")]))
        base = [{"role": "system", "content": "rules"}, {"role": "user", "content": "current"}]
        await loop._assemble_context("r", {"_attempt_id": "a", "options": {"mode": mode}}, "m", base)
        assert bool(platform.saved) == compacts

    asyncio.run(exercise())


@pytest.mark.parametrize("context,used,compacts", [(10000, 8499, False), (10000, 8500, True), (20000, 8500, False)])
def test_work_execution_uses_same_threshold(monkeypatch, context, used, compacts):
    cat = Catalog([ModelSpec("m", "m", provider="mock", context_tokens=context, max_output_tokens=1000)])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: cat)
    monkeypatch.setattr("ailab_platform.agent.loop.estimator.estimate", lambda model, value: used if "OLDER" in json.dumps(value) else 100)
    loop = AgentLoop(FakePlatform(), FakeBroker(), ScriptedLLM([LLMResponse(content="summary")]))
    state = fresh_state()
    state["messages"] = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "current"},
        {"role": "assistant", "content": "OLDER"},
        {"role": "assistant", "content": "recent one"},
        {"role": "assistant", "content": "recent two"},
    ]
    state["user_message_index"] = 1
    loop._states["r"], loop._steps["r"] = state, 0
    turn = _Turn({"prompt": "current", "_attempt_id": "a", "_ledger_tokens": 0})
    turn.messages = state["messages"]
    asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", []))
    assert bool(loop.platform.reservations) == compacts
    if compacts:
        assert state["compaction"]["threshold"] == int(context * 0.85)
        assert state["messages"][1]["content"] == "current"
