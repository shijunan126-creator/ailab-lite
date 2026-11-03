"""Full-window thresholds and per-call output allocation; no real-model capacity claim."""

import asyncio
import json

import pytest

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.loop import AgentLoop, _Turn
from ailab_platform.catalog import Catalog, ModelSpec
from ailab_platform.contracts.context_summary import ContextSummary, canonical_summary
from ailab_platform.llm import LLMResponse
from ailab_platform.model_policy import ModelPolicy
from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM


@pytest.mark.parametrize("context,expected", [(10000, 9000), (20000, 18000), (1048576, 943718)])
def test_threshold_uses_full_model_window_independent_of_maximum_output(context, expected):
    spec = ModelSpec("m", "m", context_tokens=context, max_output_tokens=context // 2)
    assert ModelPolicy.compaction_threshold(spec) == expected
    assert ModelPolicy.input_budget(spec) == context - 1
    assert ModelPolicy.output_budget(spec, context * 8 // 10) == context - context * 8 // 10
    assert ModelPolicy.output_budget(spec, context) == 0


@pytest.mark.parametrize("mode", ["chat", "work"])
@pytest.mark.parametrize("context,used,compacts", [(10000, 8999, False), (10000, 9000, True), (20000, 9000, False)])
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
        loop = AgentLoop(platform=platform, llm=ScriptedLLM([LLMResponse(content=canonical_summary(ContextSummary(objective="summary")))]))
        base = [{"role": "system", "content": "rules"}, {"role": "user", "content": "current"}]
        await loop._assemble_context("r", {"_attempt_id": "a", "options": {"mode": mode}}, "m", base)
        assert bool(platform.saved) == compacts

    asyncio.run(exercise())


@pytest.mark.parametrize("context,used,compacts", [(10000, 8999, False), (10000, 9000, True), (20000, 9000, False)])
def test_work_execution_uses_same_threshold(monkeypatch, context, used, compacts):
    cat = Catalog([ModelSpec("m", "m", provider="mock", context_tokens=context, max_output_tokens=1000)])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: cat)
    monkeypatch.setattr("ailab_platform.agent.loop.estimator.estimate", lambda model, value: used if "OLDER" in json.dumps(value) else 100)
    loop = AgentLoop(
        FakePlatform(), FakeBroker(), ScriptedLLM([LLMResponse(content=canonical_summary(ContextSummary(objective="summary")))])
    )
    state = fresh_state()
    state["messages"] = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "current"},
        {"role": "assistant", "content": "OLDER"},
        {"role": "assistant", "content": "recent one"},
        {"role": "assistant", "content": "recent two"},
    ]
    state["user_message_index"] = 2
    loop._states["r"], loop._steps["r"] = state, 0
    turn = _Turn({"prompt": "current", "_attempt_id": "a", "_ledger_tokens": 0})
    turn.messages = state["messages"]
    asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", []))
    assert bool(loop.platform.reservations) == compacts
    if compacts:
        assert state["compaction"]["threshold"] == int(context * 0.90)
        assert state["messages"][2]["content"] == "current"


@pytest.mark.parametrize("used,expected", [(8999, False), (9000, True)])
def test_persisted_context_setting_uses_ninety_percent(used, expected):
    from fastapi.testclient import TestClient

    from ailab_platform.api.main import app
    from ailab_platform.context_settings import context_settings
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import PlatformSetting
    from ailab_platform.schemas import ContextSettings

    assert ContextSettings().threshold_ratio == 0.90
    with TestClient(app), SessionLocal() as db:
        existing = db.get(PlatformSetting, "context")
        previous = dict(existing.value) if existing else None
        if existing:
            existing.value = {"threshold_ratio": 0.90}
        else:
            db.add(PlatformSetting(key="context", value={"threshold_ratio": 0.90}))
        db.commit()
        try:
            config = context_settings(db)
            spec = ModelSpec("m", "m", context_tokens=10000)
            assert (used >= ModelPolicy.compaction_threshold(spec, config)) is expected
        finally:
            if previous is not None:
                db.get(PlatformSetting, "context").value = previous
            else:
                db.delete(db.get(PlatformSetting, "context"))
            db.commit()
