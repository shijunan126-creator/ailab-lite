"""Usage is the authority; metadata never enters a model request."""

import asyncio
from copy import deepcopy

import pytest

from ailab_platform.adapters.runtime import checkpoint_state, fresh_state, restored_state
from ailab_platform.agent.context import ContextManager
from ailab_platform.agent.context_band import ContextBand, stage_context
from ailab_platform.agent.context_usage import MAX_ANCHORS, CandidatePricing, record_usage, request_tokens, valid_anchors
from ailab_platform.agent.loop import AgentLoop
from ailab_platform.catalog import Catalog, ModelSpec
from ailab_platform.contracts.context_summary import ContextSummary
from ailab_platform.llm import LLMError, LLMResponse
from ailab_platform.model_policy import estimator
from tests.test_agent_loop import FakePlatform, ScriptedLLM

PREFIX = [{"role": "system", "content": "rules"}, {"role": "system", "content": "context"}]


@pytest.mark.parametrize("actual,raw,expected", [(80000, 95000, False), (95000, 80000, True)])
def test_trigger_prices_anchor_and_only_appended_delta(monkeypatch, actual, raw, expected):
    spec = ModelSpec("m", "m", context_tokens=100000, max_output_tokens=1000)
    loop = AgentLoop(FakePlatform())
    manager = ContextManager(loop, "r", catalog=lambda: Catalog([spec]))
    messages = deepcopy(PREFIX)
    record_usage(manager.state, "m", messages, [], actual)
    monkeypatch.setattr(estimator, "estimate", lambda *_: raw)
    messages.append({"role": "user", "content": "new input"})
    assert manager.should_compact("m", messages, []) is expected
    assert request_tokens(manager.state, "m", messages, []) == actual + estimator.raw(messages[2:]) - estimator.raw([])


def test_checkpoint_restart_identity_and_bounded_metadata():
    state = fresh_state()
    messages = state["messages"] = deepcopy(PREFIX)
    for i in range(100):
        record_usage(state, "m", messages, [], 1000 + i * 100)
        messages.append({"role": "user", "content": str(i)})
    assert len(state["prompt_anchors"]) == MAX_ANCHORS
    restored = restored_state({"state": deepcopy(checkpoint_state(state, {}))})
    assert request_tokens(restored, "m", restored["messages"], []) == request_tokens(state, "m", messages, [])
    assert restored["prompt_anchors"][0]["tokens"] == 1000
    for model, tools, mutation in [("other", [], None), ("m", [{}], None), ("m", [], "epoch"), ("m", [], "abi"), ("m", [], "rebuild")]:
        changed = deepcopy(restored)
        if mutation == "epoch":
            changed["context_epoch"] += 1
        elif mutation == "abi":
            changed["context_abi_version"] += 1
        elif mutation == "rebuild":
            changed["messages"][0]["content"] = "new prefix"
        assert not valid_anchors(changed, model, changed["messages"], tools)
        assert request_tokens(changed, model, changed["messages"], tools) == estimator.estimate(
            model, {"messages": changed["messages"], "tools": tools}
        )


def test_candidate_retained_interval_actual_cost_and_new_blocks():
    state = fresh_state()
    messages = state["messages"] = deepcopy(PREFIX)
    record_usage(state, "m", messages, [], 1000)
    older = {"role": "assistant", "content": "old" * 10000}
    latest = {"role": "assistant", "content": "latest" * 2000}
    messages.extend([older, latest])
    record_usage(state, "m", messages, [], 6000)
    pricing = CandidatePricing(state, "m", messages, [])
    assert pricing.cost(messages) == 6000
    # The 5K measured interval, split proportionally, replaces raw costs.
    assert pricing.cost([latest]) - pricing.cost([]) == pytest.approx(pricing.costs[3], abs=1)
    new = {"role": "system", "content": "new checkpoint"}
    assert pricing.cost([new]) - pricing.cost([]) == pytest.approx(estimator.raw(new) - 32, abs=1)
    band = ContextBand.for_model(ModelSpec("m", "m", context_tokens=100000, max_output_tokens=4096))
    staged = stage_context(band, messages[:2], ContextSummary(), messages, state, [])
    assert older in staged.messages and latest in staged.messages  # raw estimates would exceed aim
    assert staged.measurements["after_tokens"] == pricing.cost(staged.messages)
    assert staged.measurements["after_tokens"] <= band.upper
    # Actual cost can also exceed the ceiling when the estimator would fit.
    record_usage(state, "m", messages, [], 900000)
    from ailab_platform.agent.budgets import ContextPause

    with pytest.raises(ContextPause):
        stage_context(band, messages[:2], ContextSummary(), messages, state, [])


@pytest.mark.parametrize("actual,raw,allowed", [(8000, 11000, True), (11000, 8000, False)])
def test_provider_guard_uses_anchor_even_when_estimator_disagrees(monkeypatch, actual, raw, allowed):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=10000, max_output_tokens=1000)
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    loop = AgentLoop(FakePlatform(), llm=ScriptedLLM([LLMResponse(content="ok", usage={"prompt_tokens": 8100})]))
    state = loop._states["r"] = fresh_state()
    messages = state["messages"] = deepcopy(PREFIX)
    record_usage(state, "m", messages, [], actual)
    monkeypatch.setattr(estimator, "estimate", lambda *_: raw)

    async def exercise():
        if allowed:
            await loop._call_model(loop.llm, "r", {"_attempt_id": "a"}, "m", messages, 0, [])
            assert state["prompt_anchors"][-1]["tokens"] == 8100
        else:
            with pytest.raises(LLMError, match="budget exceeded"):
                await loop._call_model(loop.llm, "r", {"_attempt_id": "a"}, "m", messages, 0, [])
            assert not loop.platform.reservations

    asyncio.run(exercise())


def test_successful_boundary_clears_anchors_and_failed_cas_keeps_them():
    async def exercise():
        loop = AgentLoop(FakePlatform())
        state = loop._states["r"] = fresh_state()
        state["messages"] = deepcopy(PREFIX)
        record_usage(state, "m", state["messages"], [], 1000)
        before = deepcopy(state["prompt_anchors"])
        original = loop._checkpoint

        async def fail(*_):
            raise RuntimeError("failed CAS")

        loop._checkpoint = fail
        from ailab_platform.agent.budgets import ContextPause

        with pytest.raises(ContextPause):
            await loop._context("r").activate_boundary({"reason": "rebuilt"})
        assert state["prompt_anchors"] == before
        loop._checkpoint = original
        await loop._context("r").activate_boundary({"reason": "rebuilt"})
        assert not state.get("prompt_anchors")

    asyncio.run(exercise())


def test_actual_call_anchor_survives_worker_restart(monkeypatch):
    from ailab_platform.agent.loop import _Turn

    spec = ModelSpec("m", "m", provider="mock", context_tokens=100000, max_output_tokens=1000)
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))

    async def exercise():
        platform = FakePlatform()
        first = AgentLoop(platform, llm=ScriptedLLM([LLMResponse(content="ok", usage={"prompt_tokens": 80000})]))
        first._states["r"] = fresh_state()
        messages = first._states["r"]["messages"] = deepcopy(PREFIX)
        run = {"_attempt_id": "a"}
        await first._call_model(first.llm, "r", run, "m", messages, 0, [])
        turn = _Turn(run)
        turn.messages = messages
        await first._checkpoint("r", turn)
        second = AgentLoop(platform, llm=ScriptedLLM([LLMResponse(content="again")]))
        second._states["r"] = restored_state(platform.checkpoint)
        restored = second._states["r"]["messages"]
        restored.append({"role": "user", "content": "continue"})
        expected = 80000 + estimator.raw(restored[2:]) - estimator.raw([])
        await second._call_model(second.llm, "r", run, "m", restored, 1, [])
        assert list(platform.reservations.values())[-1]["estimated_input_tokens"] == expected

    asyncio.run(exercise())


def test_boundary_prices_sent_summary_prefix_and_estimates_tombstones():
    state = fresh_state()
    old = {"role": "assistant", "content": "history" * 10000}
    source = [*deepcopy(PREFIX), old, {"role": "user", "content": "summarize"}]
    record_usage(state, "m", source, [], 5000)
    retained = deepcopy(source[:-1])
    pricing = CandidatePricing(state, "m", source, [], aliases=retained)
    assert pricing.cost(retained) < 5000
    tombstone = {"role": "assistant", "content": "history removed"}
    assert pricing.cost([tombstone]) - pricing.cost([]) == pytest.approx(estimator.raw(tombstone) - 32, abs=1)
    band = ContextBand.for_model(ModelSpec("m", "m", context_tokens=100000, max_output_tokens=4096))
    staged = stage_context(band, retained[:2], ContextSummary(), retained, state, [], pricing_source=source)
    assert old in staged.messages
    assert staged.measurements["after_tokens"] < 10000
