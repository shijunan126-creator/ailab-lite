"""Whole-request budgets, not a percentage cut or summary quality simulation."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.budgets import ContextPause
from ailab_platform.agent.context_band import ContextBand, active_users, admit_tool_result, bounded_runtime, stage_context
from ailab_platform.agent.context_protocol import protocol_groups
from ailab_platform.agent.context_rendering import block, loaded_modules
from ailab_platform.agent.loop import AgentLoop, _Turn
from ailab_platform.agent.tools import tool_result_message
from ailab_platform.catalog import Catalog, ModelSpec
from ailab_platform.clients import ClientError
from ailab_platform.config import ContextBandSettings, Settings
from ailab_platform.contracts.context_summary import ContextSummary, SummaryEntry, SummarySource, canonical_summary
from ailab_platform.llm import LLMResponse, ToolCall
from tests.test_agent_loop import FakePlatform, ScriptedLLM

WINDOWS = [32000, 64000, 128000, 300000, 1000000]
PREFIX = [{"role": "system", "content": "rules"}, {"role": "system", "content": "context"}]
CURRENT = {"role": "user", "content": "current constraints and document references"}


def band_for(window):
    return ContextBand.for_model(ModelSpec("band", "band", context_tokens=window, max_output_tokens=8192))


def pair(index, tokens=2000):
    return [
        {"role": "assistant", "tool_calls": [{"id": str(index), "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": str(index), "name": "read_file", "content": "汉" * tokens},
    ]


def fixture(window):
    band = band_for(window)
    target = min(band.trigger, window - 4096 - min(2048, window // 16)) + 100
    messages = [*copy.deepcopy(PREFIX), copy.deepcopy(CURRENT)]
    group_cost = band.cost(pair(0)) - band.cost([])
    count = max(0, (target - band.cost({"messages": messages, "tools": []})) // group_cost)
    messages.extend(m for i in range(count) for m in pair(i))
    remaining = target - band.cost({"messages": messages, "tools": []})
    messages.extend(pair(count, max(1, remaining - 90)))
    return messages


@pytest.mark.parametrize("window", WINDOWS)
def test_policy_scaling_and_environment_validation(window):
    band = band_for(window)
    assert (band.lower, band.upper, band.ceiling, band.aim, band.trigger) == (
        min(10000, int(window * 0.15)),
        min(48000, int(window * 0.55)),
        min(64000, int(window * 0.75)),
        min(30000, int(window * 0.55)),
        int(window * 0.90),
    )
    with pytest.raises(ValidationError):
        ContextBandSettings(target_upper_tokens=5000)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, lower_window_ratio=0.6, upper_window_ratio=0.4)


@pytest.mark.parametrize("window", WINDOWS)
@pytest.mark.parametrize("path", ["run", "parent", "rebuild"])
def test_all_paths_stage_whole_request_in_band(monkeypatch, window, path):
    spec = ModelSpec("band", "band", provider="mock", context_tokens=window, max_output_tokens=8192)
    cat = Catalog([spec])
    messages = fixture(window)
    latest_id = messages[-1]["tool_call_id"]
    history = [{"id": f"h{i}", "prompt": "汉" * 1900, "reply": "reply"} for i in range(int(window * 0.9) // 1900 + 1)]

    class Platform(FakePlatform):
        async def get_model_context(self, *args, **kwargs):
            return {"summary": "", "through_run_id": None, "history": history, "has_more": False}

        async def save_model_summary(self, run_id, payload):
            # It must have passed the shared exact-candidate validator first.
            self.saved.append(payload)

    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: cat)
    platform = Platform()
    platform.saved = []
    loop = AgentLoop(platform=platform, llm=ScriptedLLM([LLMResponse(content=canonical_summary(ContextSummary(objective="goal")))] * 4))
    state = fresh_state()
    state.update(messages=messages, user_message_index=2)
    loop._states["r"] = state
    run = {"model": "band", "_attempt_id": "a", "_fixed_catalog": []}
    manager = loop._context("r")

    async def checkpoint(*args):
        pass

    monkeypatch.setattr(loop, "_checkpoint", checkpoint)
    if path == "run":
        asyncio.run(manager.compact("r", SimpleNamespace(messages=messages, run=run), loop.llm, "band", []))
        result = messages
        assert any(m.get("tool_call_id") == latest_id for m in result)
    elif path == "parent":
        result = asyncio.run(manager._compact_parent_context("r", run, loop.llm, "band", messages, CURRENT, []))
        assert result[-1] == CURRENT
        assert any(m.get("tool_call_id") == latest_id for m in result)
    else:
        result = asyncio.run(manager._assemble_context("r", run, "band", [*PREFIX, CURRENT]))
        assert platform.saved
    band = band_for(window)
    total = band.cost({"messages": result, "tools": []})
    assert band.lower <= total <= band.upper <= band.ceiling
    protocol_groups(result)
    assert len(loop.llm.seen_messages) == 1


@pytest.mark.parametrize("window", WINDOWS)
def test_zero_optional_groups_does_not_pad(window):
    band = band_for(window)
    staged = stage_context(band, PREFIX, ContextSummary(), [*PREFIX, CURRENT], {}, [], protected={2})
    assert staged.measurements["after_tokens"] < band.lower
    assert staged.measurements["retained_groups"] == 0
    assert CURRENT in staged.messages


@pytest.mark.parametrize("window,chars,expected", [(1000000, 150000, "over_ceiling_current_input"), (128000, 120000, "pause")])
def test_irreducible_current_and_all_steers_docrefs(window, chars, expected):
    band = band_for(window)
    current = {"role": "user", "content": "汉" * chars}
    steer = {"role": "user", "content": "steer with document quote " + "引" * 4000}
    messages = [*PREFIX, current, *pair(1), steer, *pair(2)]
    protected = active_users(messages, 2)
    assert protected == {2, 5}
    if expected == "pause":
        with pytest.raises(ContextPause):
            stage_context(band, PREFIX, ContextSummary(), messages, {}, [], protected)
    else:
        staged = stage_context(band, PREFIX, ContextSummary(), messages, {}, [], protected)
        assert staged.measurements["reason"] == expected
        assert band.ceiling < staged.measurements["after_tokens"] < band.trigger
        assert staged.measurements["retained_groups"] == 0
        assert current in staged.messages and steer in staged.messages


@pytest.mark.parametrize("image_count", [12, 16])
def test_images_follow_latest_complete_semantic_batch(image_count):
    band = band_for(128000)
    messages = [*PREFIX, CURRENT]
    image = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,eA=="}}
    for i in range(image_count // 4):
        messages.extend(pair(i, 100))
        messages.append({"role": "user", "content": [image] * 4})
        messages.append({"role": "assistant", "content": f"continuation-{i}"})
    staged = stage_context(band, PREFIX, ContextSummary(), messages, {}, [], protected={2})
    assert staged.measurements["after_tokens"] <= band.upper
    assert staged.messages[-1]["content"] == f"continuation-{image_count // 4 - 1}"
    assert (
        sum(p.get("type") == "image_url" for m in staged.messages for p in (m["content"] if isinstance(m.get("content"), list) else []))
        >= 4
    )
    protocol_groups(staged.messages)


def test_admission_accounts_batch_args_results_images_and_whole_file():
    band = band_for(1000000)
    calls = [ToolCall(str(i), "load_skill", {"name": "platform.pdf", "resource": "script.py"}) for i in range(3)]
    messages = [
        *PREFIX,
        CURRENT,
        {
            "role": "assistant",
            "tool_calls": [{"id": c.id, "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in calls],
        },
    ]
    state = {"user_message_index": 2, "pending": [{"id": c.id, "name": c.name} for c in calls]}
    accepted = []
    for call in calls:
        result = admit_tool_result(band, messages, [], state, call, {"ok": True, "skill": "汉" * 22000})
        accepted.append(result)
        messages.append(tool_result_message(call.id, call.name, result))
        state["pending"].pop(0)
    assert accepted[0]["ok"]
    assert not accepted[1]["ok"] and accepted[1]["reason"] == "batch_budget" and "skill" not in accepted[1]
    assert band.cost(messages[3:]) < 64000
    protocol_groups(messages)
    huge = ToolCall("huge", "read_file", {})
    result = admit_tool_result(
        band, [*PREFIX, CURRENT, pair("huge")[0]], [], {"user_message_index": 2}, huge, {"ok": True, "text": "汉" * 80000}
    )
    assert not result["ok"] and "text" not in result
    image_state = {"user_message_index": 2, "pending_image_inputs": [[{"type": "image_url", "image_url": {"url": "x"}}] * 16]}
    result = admit_tool_result(band, [*PREFIX, CURRENT, pair("huge")[0]], [], image_state, huge, {"ok": True})
    assert not result["ok"] and image_state["pending_image_inputs"] == []


def test_900k_audit_source_admission_prevents_the_80k_latest_group():
    band = band_for(1000000)
    messages = [*PREFIX, CURRENT, *(m for i in range(101) for m in pair(i, 8100))]
    call = ToolCall("latest", "read_file", {})
    messages.append(pair("latest")[0])
    result = admit_tool_result(band, messages, [], {"user_message_index": 2}, call, {"ok": True, "text": "汉" * 80000})
    assert not result["ok"]
    messages.append(tool_result_message(call.id, call.name, result))
    staged = stage_context(band, PREFIX, ContextSummary(), messages, {}, [], {2})
    assert band.lower <= staged.measurements["after_tokens"] <= 48000
    assert any(m.get("tool_call_id") == "latest" for m in staged.messages)
    # Legacy already-inserted oversized groups cannot be silently rewritten.
    with pytest.raises(ContextPause, match="最近完整工具组"):
        stage_context(band, PREFIX, ContextSummary(), [*PREFIX, CURRENT, *pair("legacy", 80000)], {}, [], {2})


def test_runtime_and_module_saturation_keep_fences_and_active_handles():
    band = band_for(128000)
    state = {
        "plan": [{"id": str(i), "title": "汉" * 500, "status": "running" if i == 99 else "pending"} for i in range(100)],
        "delivered": {f"/workspace/outputs/{i}.txt": {"sha256": "a" * 64} for i in range(1000)},
        "background_jobs": ["live"],
        "file_wait_deadlines": {"pending": 99},
    }
    before = copy.deepcopy(state)
    runtime = bounded_runtime(band, state)
    value = json.loads(runtime["content"].split("\n")[1])
    assert value["delivery_total"] == 1000
    assert value["confirmed_deliveries"]["omitted_count"] > 0
    assert value["plan"]["items"][0]["id"] == "99" and value["plan"]["omitted_count"] > 0
    assert "live" in runtime["content"] and "pending" in runtime["content"]
    assert state == before
    modules = [{"name": "n" * 64, "resource": f"{i:03}" + "r" * 509, "hash": "a" * 64} for i in range(512)]
    staged = stage_context(band, PREFIX, ContextSummary(), [*PREFIX, block("loaded_modules", modules), CURRENT], state, [], {3})
    nav = next(m for m in staged.messages if m.get("content", "").startswith("<loaded_modules>"))
    assert band.cost(nav) <= band.allocation(2000, 0.04)
    assert len(loaded_modules([nav])) < 512 and "omitted_count" in nav["content"]
    with pytest.raises(ContextPause, match="活动状态"):
        bounded_runtime(band, {"background_jobs": [str(i) + "a" * 100 for i in range(1000)]})


@pytest.mark.parametrize("extra,id_length,valid", [(False, 64, True), (False, 65, False), (True, 64, False)])
def test_plan_input_maximum_ids_and_extra_keys(monkeypatch, extra, id_length, valid):
    loop = AgentLoop(platform=FakePlatform(), llm=ScriptedLLM([]))
    loop._states["r"] = fresh_state()
    steps = [
        {"id": str(i).zfill(id_length), "title": "汉" * 500, "status": "pending", **({"extra": "x"} if extra else {})} for i in range(100)
    ]
    turn = _Turn({})

    async def nop(*args):
        pass

    monkeypatch.setattr(loop, "_checkpoint", nop)
    result = asyncio.run(loop._durable_tool(turn, "r", ToolCall("p", "update_plan", {"steps": steps}), 1, "1:0"))
    assert result["ok"] is valid
    assert bool(loop._states["r"]["plan"]) is valid


def test_two_compactions_refresh_memory_no_stale_blocks_and_cas_rollback(monkeypatch):
    cat = Catalog([ModelSpec("band", "band", provider="mock", context_tokens=64000, max_output_tokens=8192)])
    latest_memory = {"enabled": True, "revision": 2, "entries": [{"id": str(i), "kind": "user", "content": "汉" * 1000} for i in range(20)]}

    class Platform(FakePlatform):
        async def get_memory_context(self, run_id):
            return copy.deepcopy(latest_memory)

    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: cat)
    semantic = ContextSummary(facts=[SummaryEntry(id=str(i), text="汉" * 1000, sources=[SummarySource(run_id="r")]) for i in range(4)])
    loop = AgentLoop(platform=Platform(), llm=ScriptedLLM([LLMResponse(content=canonical_summary(semantic))] * 3))
    state = fresh_state()
    state.update(messages=fixture(64000), user_message_index=2, memory_observed={"revision": 1})
    loop._states["r"] = state
    manager = loop._context("r")
    turn = SimpleNamespace(messages=state["messages"], run={"model": "band", "_attempt_id": "a"})

    async def saved(*args):
        pass

    monkeypatch.setattr(loop, "_checkpoint", saved)

    def grow(label):
        band = band_for(64000)
        target = band.trigger + 100
        i = 0
        while band.cost({"messages": state["messages"], "tools": []}) < target:
            remaining = target - band.cost({"messages": state["messages"], "tools": []})
            state["messages"].extend(pair(f"{label}-{i}", min(2000, max(1, remaining - 70))))
            i += 1

    for iteration in range(2):
        if iteration:
            grow("next")
        asyncio.run(manager.compact("r", turn, loop.llm, "band", []))
        contents = [m.get("content", "") for m in state["messages"] if m.get("role") == "system"]
        assert all(
            sum(text.startswith(tag) for text in contents) == 1
            for tag in ("<conversation_checkpoint>", "<runtime_state>", "<loaded_modules>")
        )
        assert state["compaction"]["checkpoint_tokens"] <= band_for(64000).summary_budget + 64
        before = copy.deepcopy(state["messages"])
        asyncio.run(manager.refresh_memory("r", state["messages"]))
        assert state["messages"] == before and state["memory_observed"]["revision"] == 2
    grow("cas")
    before = copy.deepcopy(state)

    async def conflict(*args):
        raise ClientError("PUT", "/checkpoint", 409, "conflict")

    monkeypatch.setattr(loop, "_checkpoint", conflict)
    with pytest.raises(ClientError):
        asyncio.run(manager.compact("r", turn, loop.llm, "band", []))
    assert state == before


def test_unchanged_irreducible_retry_does_not_spend_again(monkeypatch):
    cat = Catalog([ModelSpec("band", "band", provider="mock", context_tokens=128000, max_output_tokens=8192)])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: cat)
    loop = AgentLoop(platform=FakePlatform(), llm=ScriptedLLM([LLMResponse(content=canonical_summary(ContextSummary()))]))
    messages = [*PREFIX, {"role": "user", "content": "汉" * 116000}]
    state = fresh_state()
    state.update(messages=messages, user_message_index=2)
    loop._states["r"] = state
    turn = SimpleNamespace(messages=messages, run={"model": "band", "_attempt_id": "a"})
    for _ in range(2):
        with pytest.raises(ContextPause):
            asyncio.run(loop._context("r").compact("r", turn, loop.llm, "band", []))
    assert len(loop.llm.seen_messages) == 1
    assert state["context_epoch"] == 0 and state["failed_compaction_digest"]


def test_all_active_multimodal_users_protected_with_tool_images_distinguished():
    image = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,eA=="}}
    first = {"role": "user", "content": [{"type": "text", "text": "current attachment"}, image]}
    steer = {"role": "user", "content": [image]}
    tool_image = {"role": "user", "content": [{"type": "text", "text": "tool page"}, image]}
    messages = [*PREFIX, first, *pair(1, 10), tool_image, steer, *pair(2, 10)]
    protected = active_users(messages, 2, {5})
    assert protected == {2, 6}
    staged = stage_context(band_for(128000), PREFIX, ContextSummary(), messages, {}, [], protected)
    assert all(message in staged.messages for message in (first, steer))
    assert any(message.get("tool_call_id") == "2" for message in staged.messages)
    protocol_groups(staged.messages)


def test_plan_rejects_unknown_top_level_keys(monkeypatch):
    loop = AgentLoop(platform=FakePlatform(), llm=ScriptedLLM([]))
    loop._states["r"] = fresh_state()

    async def noop(*args):
        pass

    monkeypatch.setattr(loop, "_checkpoint", noop)
    result = asyncio.run(loop._durable_tool(_Turn({}), "r", ToolCall("p", "update_plan", {"steps": [], "extra": "x"}), 1, "1:0"))
    assert not result["ok"]


def test_pending_batch_reserves_real_cannot_fit_messages():
    band = band_for(1000000)
    calls = [ToolCall(str(i), "read_file", {}) for i in range(20)]
    assistant = {"role": "assistant", "tool_calls": [{"id": call.id, "function": {"name": call.name, "arguments": "{}"}} for call in calls]}
    messages = [*PREFIX, CURRENT, assistant]
    state = {"user_message_index": 2, "pending": [{"id": call.id, "name": call.name} for call in calls]}
    from ailab_platform.agent.context_band import CANNOT_FIT_RESULT, batch_budget

    reserve = band.cost([tool_result_message(call.id, call.name, CANNOT_FIT_RESULT) for call in calls[1:]])
    envelope = band.cost([assistant, tool_result_message(calls[0].id, calls[0].name, {"ok": True, "text": ""})])
    payload_tokens = batch_budget(band, messages, [], state) - reserve - envelope + 1
    assert payload_tokens > 0
    first = None
    for index, call in enumerate(calls):
        result = admit_tool_result(band, messages, [], state, call, {"ok": True, "text": "汉" * (payload_tokens if index == 0 else 80000)})
        first = result if first is None else first
        messages.append(tool_result_message(call.id, call.name, result))
        state["pending"].pop(0)
    assert not first["ok"]
    assert band.cost(messages[3:]) <= band.allocation(24000, 0.4)
    protocol_groups(messages)


def test_uncompacted_rebuild_bounds_deterministic_blocks_without_changing_trigger(monkeypatch):
    from ailab_platform.contracts.context_summary import ReadModule

    cat = Catalog([ModelSpec("band", "band", provider="mock", context_tokens=1000000, max_output_tokens=8192)])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: cat)
    summary = ContextSummary(read_modules=[ReadModule(name="n" * 64, resource=f"{i:03}" + "r" * 509, hash="a" * 64) for i in range(512)])

    class Platform(FakePlatform):
        async def get_model_context(self, *args, **kwargs):
            return {"summary": canonical_summary(summary), "through_run_id": "old", "history": [], "has_more": False}

    loop = AgentLoop(platform=Platform(), llm=ScriptedLLM([]))
    loop._states["r"] = {**fresh_state(), "plan": [{"id": str(i), "title": "汉" * 500, "status": "pending"} for i in range(100)]}
    result = asyncio.run(loop._assemble_context("r", {"model": "band", "_fixed_catalog": []}, "band", [*PREFIX, CURRENT]))
    nav = next(message for message in result if message.get("content", "").startswith("<loaded_modules>"))
    assert band_for(1000000).cost(nav) <= 2000
    runtime = next(message for message in result if message.get("content", "").startswith("<runtime_state>"))
    assert json.loads(runtime["content"].split("\n")[1])["plan"]["omitted_count"] > 0
    assert not loop.llm.seen_messages


def test_compaction_before_measurement_includes_recall_body(monkeypatch):
    cat = Catalog([ModelSpec("band", "band", provider="mock", context_tokens=64000, max_output_tokens=8192)])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: cat)
    loop = AgentLoop(platform=FakePlatform(), llm=ScriptedLLM([LLMResponse(content=canonical_summary(ContextSummary()))]))
    messages = fixture(64000)
    messages[3]["tool_calls"][0]["function"]["name"] = "recall_history"
    messages[4]["name"] = "recall_history"
    before = band_for(64000).cost({"messages": messages, "tools": []})
    loop._states["r"] = {**fresh_state(), "messages": messages, "user_message_index": 2}

    async def checkpoint(*args):
        pass

    monkeypatch.setattr(loop, "_checkpoint", checkpoint)
    asyncio.run(
        loop._context("r").compact("r", SimpleNamespace(messages=messages, run={"model": "band", "_attempt_id": "a"}), loop.llm, "band", [])
    )
    assert loop._states["r"]["compaction"]["before_tokens"] == before


@pytest.mark.parametrize("prefix_tokens", [61500, 62500])
def test_optional_navigation_yields_before_latest_complete_unit(prefix_tokens):
    band = band_for(128000)
    prefix = [{"role": "system", "content": "汉" * prefix_tokens}, PREFIX[1]]
    modules = [{"name": "n" * 64, "resource": f"{i:03}" + "r" * 509, "hash": "a" * 64} for i in range(512)]
    prior = [*prefix, block("loaded_modules", modules), CURRENT, *pair("latest", 1000)]
    staged = stage_context(band, prefix, ContextSummary(), prior, {}, [], {3})
    assert staged.measurements["reason"] == "band"
    assert staged.measurements["after_tokens"] <= band.ceiling
    assert any(message.get("tool_call_id") == "latest" for message in staged.messages)


def test_legacy_module_marker_is_replaced_not_retained_as_latest_group():
    from ailab_platform.agent.skill_modules import SUMMARY_MARKER

    entries = [["n" * 64, f"{i:03}" + "r" * 509, "a" * 64] for i in range(512)]
    marker = {"role": "system", "content": SUMMARY_MARKER + json.dumps(entries)}
    prior = [*PREFIX, CURRENT, *pair("latest", 100), marker]
    staged = stage_context(band_for(128000), PREFIX, ContextSummary(), prior, {}, [], {2})
    assert not any(SUMMARY_MARKER in message.get("content", "") for message in staged.messages if isinstance(message.get("content"), str))
    assert any(message.get("tool_call_id") == "latest" for message in staged.messages)
    assert staged.measurements["after_tokens"] <= 48000
