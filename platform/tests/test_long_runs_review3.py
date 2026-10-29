"""Independent review regressions: budgets, checkpoint bounds and process lifecycle."""

import asyncio
import json

import pytest

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.budgets import WorkBudget
from ailab_platform.agent.loop import AgentLoop, _Turn
from ailab_platform.agent.stuck import compact_log, observation
from ailab_platform.config import settings
from ailab_platform.llm import LLMResponse, ToolCall
from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM, _run


def test_consumed_log_multi_window_size():
    state = fresh_state()
    for step in range(1200):
        state["tool_log"][f"{step}:0"] = observation("read_file", {"path": str(step)}, {"ok": True, "text": "中" * 1300})
    before = len(json.dumps(state, ensure_ascii=False).encode())
    compact_log(state)
    after = len(json.dumps(state, ensure_ascii=False).encode())
    print(f"multi-window log bytes: {before} -> {after}")
    assert after < 500000
    assert all(r["result"] == {"ok": True, "compacted": True} for r in state["tool_log"].values())


def test_cleanup_failure_does_not_fail_success():
    class Broker(FakeBroker):
        async def exec(self, *args, **kw):
            raise RuntimeError("cleanup unavailable")

    model = ScriptedLLM([LLMResponse(tool_calls=[ToolCall("l", "list_files", {})]), LLMResponse(content="完成")])
    platform, _ = _run(model, broker=Broker())
    assert platform.statuses[-1][0] == "succeeded"


@pytest.mark.parametrize("stage", ["_finish_previews", "_deliver_leftovers", "_workspace_checkpoint"])
def test_pause_cleanup_failure_still_pauses(stage):
    platform = FakePlatform()
    loop = AgentLoop(platform, FakeBroker())
    state = fresh_state()
    loop._states["r"], loop._steps["r"] = state, 0
    loop._budgets["r"] = WorkBudget(state, "j")
    turn = _Turn({})
    turn.lease_id = "lease"

    async def fail(*args, **kw):
        raise RuntimeError("cleanup unavailable")

    setattr(loop, stage, fail)
    asyncio.run(loop._budget_pause("r", turn, "暂停"))
    assert platform.paused["status"] == "interrupted"
    assert platform.paused["result"]["workspace_saved"] is False


def test_token_cap_is_terminal_and_preserves_sources(monkeypatch):
    monkeypatch.setattr(settings, "run_max_tokens", 1)
    platform = FakePlatform()
    platform.prior_usage = {"prompt_tokens": 1}
    _run(ScriptedLLM([]), platform)
    assert platform.paused is None
    assert platform.statuses[-1][0] == "failed"
    assert "新任务" in platform.statuses[-1][1]["error"]


def test_finished_answer_crossing_time_budget_succeeds():
    platform = FakePlatform()

    class Model(ScriptedLLM):
        async def chat(self, *args, **kw):
            loop._budgets["run_1"].data["active_seconds"] = settings.run_max_seconds
            return await super().chat(*args, **kw)

    loop = AgentLoop(platform, FakeBroker(), Model([LLMResponse(content="完成")]))
    asyncio.run(loop.process("run_1", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert platform.paused is None


def test_out_of_steps_summary_not_repeated_after_redelivery(monkeypatch):
    monkeypatch.setattr(settings, "max_agent_steps", 1)
    platform = FakePlatform()
    _run(ScriptedLLM([LLMResponse(tool_calls=[ToolCall("l", "list_files", {})]), LLMResponse(content="进度")]), platform)
    model = ScriptedLLM([])
    _run(model, platform, attempt_id="retry")
    assert not model.seen_messages
    assert platform.paused["status"] == "interrupted"


def test_context_nonconvergence_pauses_before_second_paid_compaction(monkeypatch):
    from ailab_platform.agent.budgets import BudgetPause
    from ailab_platform.catalog import Catalog, ModelSpec
    from ailab_platform.model_policy import ModelPolicy

    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([ModelSpec("m", "m", provider="mock")]))
    monkeypatch.setattr(ModelPolicy, "input_budget", staticmethod(lambda spec: 10000))
    state = fresh_state()
    state["messages"] = [{"role": "system", "content": "s"}, {"role": "user", "content": "original"}]
    state["user_message_index"] = 1
    for _ in range(5):
        state["messages"].append({"role": "assistant", "content": "x" * 50000})
    loop = AgentLoop(FakePlatform(), FakeBroker(), ScriptedLLM([LLMResponse(content="summary") for _ in range(30)]))
    loop._states["r"], loop._steps["r"] = state, 0
    turn = _Turn({"prompt": "original", "_ledger_tokens": 0, "_attempt_id": "a"})
    turn.messages = state["messages"]
    with pytest.raises(BudgetPause, match="上下文"):
        asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", []))
    assert state["compaction"]["after"] >= state["compaction"]["threshold"]
    paid = len(loop.platform.reservations)
    with pytest.raises(BudgetPause, match="上下文"):
        asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", []))
    assert len(loop.platform.reservations) == paid


def test_oversized_checkpoint_pauses_instead_of_413(monkeypatch):
    monkeypatch.setattr(settings, "checkpoint_max_bytes", 10000)
    platform = FakePlatform()
    model = ScriptedLLM([LLMResponse(content="x" * 20000)])
    _run(model, platform)
    assert platform.paused and platform.paused["status"] == "interrupted"
    assert len(json.dumps(platform.checkpoint["state"], ensure_ascii=False).encode()) <= settings.checkpoint_max_bytes


def test_original_user_index_comes_from_real_paginated_assembly(monkeypatch):
    from ailab_platform.catalog import Catalog, ModelSpec

    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([ModelSpec("m", "m", provider="mock")]))

    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {
                **await super().get_run(run_id),
                "prompt": "current request",
                "history": [{"prompt": f"old {i}", "summary": "old answer"} for i in range(12)],
            }

        async def get_model_context(self, run_id, after=None):
            return {
                "summary": "prior summary",
                "through_run_id": "old",
                "history": [{"id": "p", "prompt": "paged history", "summary": "paged answer"}] if after is None else [],
                "has_more": after is None,
            }

    platform = Platform()
    _run(ScriptedLLM([LLMResponse(content="done")]), platform)
    state = platform.checkpoint["state"]
    original = state["messages"][state["user_message_index"]]
    assert original["role"] == "user" and original["content"] == "current request"
    assert state["messages"][1]["content"].startswith("历史摘要")


def test_running_polls_do_not_checkpoint_workspace(monkeypatch):
    monkeypatch.setattr("ailab_platform.agent.loop.WORKSPACE_DEBOUNCE_SECONDS", 0)

    class Broker(FakeBroker):
        async def exec(self, lease, command, timeout=None):
            from tests.runtime_fakes import background_request

            payload = background_request(self, command)
            return {
                "exit_code": 0,
                "stdout": json.dumps({"ok": True, "running": payload["action"] == "wait", "handle": "a" * 32, "exit_code": None}),
            }

    platform, broker = FakePlatform(), Broker()
    responses = [LLMResponse(tool_calls=[ToolCall(str(i), "exec_wait", {"handle": "a" * 32, "seconds": 0})]) for i in range(6)]
    _run(ScriptedLLM([*responses, LLMResponse(content="done")]), platform, broker)
    assert broker.snapshots == 1  # final cleanup only


def test_wrap_up_exec_has_bounded_useful_lifetime(monkeypatch):
    captured = []

    async def execute(name, args, lease, broker, ctx):
        captured.append(ctx.exec_lifetime_seconds)
        return {"ok": True}

    monkeypatch.setattr("ailab_platform.agent.loop.execute_tool", execute)
    loop = AgentLoop(FakePlatform(), FakeBroker())
    state = fresh_state()
    loop._states["r"], loop._steps["r"] = state, 0
    budget = loop._budgets["r"] = WorkBudget(state, "j")
    budget.data.update(active_seconds=settings.run_max_seconds, pause_step=3)
    turn = _Turn({})
    turn.lease_id = "lease"
    asyncio.run(loop._run_tool(turn, "r", ToolCall("c", "exec", {"cmd": "true"}), 0))
    assert captured == [120]
