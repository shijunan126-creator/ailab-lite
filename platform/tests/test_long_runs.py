"""Long work pauses safely; duration is independent of ownership and progress."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.budgets import BudgetPause, WorkBudget
from ailab_platform.agent.loop import AgentLoop
from ailab_platform.config import Settings, settings
from ailab_platform.llm import LLMResponse, ToolCall
from tests.runtime_fakes import background_request
from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM, _run


def test_defaults_and_config_validation():
    config = Settings(_env_file=None)
    assert config.max_agent_steps == 300
    assert config.run_max_tokens == 2_000_000
    for changes in (
        {"max_agent_steps": 10001},
        {"job_lease_seconds": 119},
        {"run_max_seconds": 86401},
        {"run_stall_seconds": 600},
        {"run_stall_seconds": 1200, "llm_total_timeout_seconds": 1201},
        {"exec_background_max_jobs": 17},
        {"exec_background_log_bytes": 1024},
    ):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **changes)


def test_hourly_self_check_has_no_progress_gate_and_old_checkpoint_resumes(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("ailab_platform.agent.budgets.time.monotonic", lambda: clock[0])
    state = fresh_state()
    state["budget"] = {"window_id": "job", "active_seconds": 0, "start_step": 0, "review_at": 3600, "review_progress": 2}
    state["progress_marks"] = ["plan:1"]
    budget = WorkBudget(state, "job")
    assert "review_at" not in budget.data and "progress_marks" not in state
    clock[0] = 3600
    assert budget.boundary() == ["self_check"]
    clock[0] = 7200
    assert budget.boundary() == ["self_check"]
    assert "pause_step" not in budget.data
    clock[0] = 14400
    assert budget.boundary() == ["wrap_up"]
    state["next_step"] = 3
    with pytest.raises(BudgetPause, match="已工作4小时"):
        budget.boundary()


def test_active_window_excludes_queue_and_survives_crash_but_resets_on_continue(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("ailab_platform.agent.budgets.time.monotonic", lambda: clock[0])
    state = fresh_state()
    budget = WorkBudget(state, "job")
    clock[0] = 60
    budget.update()
    budget.suspended = True
    clock[0] = 600
    budget.update()
    budget.suspended = False
    clock[0] = 660
    budget.update()
    assert budget.data["active_seconds"] == 120
    state["next_step"] = 140
    assert WorkBudget(state, "job").data["active_seconds"] == 120
    continued = WorkBudget(state, "continuation")
    assert continued.data["active_seconds"] == 0 and continued.data["start_step"] == 140


def test_hard_cap_wraps_but_a_finished_answer_succeeds(monkeypatch):
    class Model(ScriptedLLM):
        async def chat(self, *args, **kwargs):
            response = await super().chat(*args, **kwargs)
            # Simulate active work during a response, independent of wall clock/test duration.
            loop._budgets["run_1"].data["active_seconds"] = settings.run_max_seconds
            return response

    platform, broker = FakePlatform(), FakeBroker()
    model = Model(
        [
            LLMResponse(tool_calls=[ToolCall("w", "write_file", {"path": "/workspace/outputs/a", "content": "a"})]),
            LLMResponse(content="已保存，仍需检查"),
        ]
    )
    loop = AgentLoop(platform, broker, model)
    asyncio.run(loop.process("run_1", attempt_id="a"))
    assert platform.paused is None
    assert platform.statuses[-1][0] == "succeeded"
    assert platform.checkpoint["state"]["workspace"]
    assert broker.snapshots > 0
    assert {p["kind"] for t, p in platform.events if t == "agent.budget"} == {"wrap_up"}


def test_hourly_self_check_reaches_model_without_event():
    platform = FakePlatform()

    class Model(ScriptedLLM):
        async def chat(self, *args, **kwargs):
            response = await super().chat(*args, **kwargs)
            loop._budgets["run_1"].data["active_seconds"] = 3600
            return response

    model = Model(
        [LLMResponse(tool_calls=[ToolCall("w", "write_file", {"path": "/workspace/a", "content": "a"})]), LLMResponse(content="完成")]
    )
    loop = AgentLoop(platform, FakeBroker(), model)
    asyncio.run(loop.process("run_1", attempt_id="a"))
    kinds = [p["kind"] for t, p in platform.events if t == "agent.budget"]
    assert kinds == []
    assert any("对照计划" in str(m.get("content")) for m in model.seen_messages[-1])


def test_stalled_tool_cancels_and_preserves_uncertain_intent(monkeypatch):
    monkeypatch.setattr(settings, "run_stall_seconds", 0.03)
    monkeypatch.setattr("ailab_platform.agent.loop.CANCEL_POLL_SECONDS", 0.01)
    canceled = []

    class Hung(FakeBroker):
        async def exec(self, lease_id, command, timeout=None):
            if background_request(self, command)["action"] == "kill_all":
                return await super().exec(lease_id, command, timeout)
            try:
                await asyncio.Event().wait()
            finally:
                canceled.append(True)

    model = ScriptedLLM([LLMResponse(tool_calls=[ToolCall("e", "exec", {"cmd": "sleep 100"})])])
    platform, _broker = _run(model, broker=Hung())
    assert canceled and "长时间未收到" in platform.paused["error"]
    assert platform.paused["auto_resume"] is False
    state = platform.checkpoint["state"]
    assert state["tool_log"]["0:0"] == {"name": "exec", "started": True}
    assert state["workspace"] and state["pending"]


def test_streamed_progress_prevents_stall(monkeypatch):
    monkeypatch.setattr(settings, "run_stall_seconds", 0.025)
    monkeypatch.setattr("ailab_platform.agent.loop.CANCEL_POLL_SECONDS", 0.005)
    platform = FakePlatform()
    loop = AgentLoop(platform, FakeBroker())
    loop._budgets["r"] = WorkBudget(fresh_state(), "j")

    async def ongoing():
        for _ in range(8):
            await asyncio.sleep(0.01)
            await loop._event("r", "agent.delta", {"text": "进展"})
        return 1

    assert asyncio.run(loop._interruptible("r", ongoing())) == 1


def test_step_budget_resumes_with_new_window_and_keeps_lifetime_usage(monkeypatch):
    monkeypatch.setattr(settings, "max_agent_steps", 1)

    class Platform(FakePlatform):
        window = "j1"

        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "budget_window_id": self.window}

    platform = Platform()
    _run(ScriptedLLM([LLMResponse(tool_calls=[ToolCall("l", "list_files", {})]), LLMResponse(content="还需继续")]), platform)
    assert platform.paused["status"] == "interrupted"
    assert platform.checkpoint["state"]["next_step"] == 1
    platform.window = "j2"
    model = ScriptedLLM([LLMResponse(content="全部完成")])
    _run(model, platform, attempt_id="a2")
    assert len(model.seen_messages) == 1 and platform.statuses[-1][0] == "succeeded"
    assert any("此前收尾限制已解除" in str(m.get("content")) for m in model.seen_messages[0])


def test_current_run_context_compacts_whole_tool_groups_and_keeps_original_input(monkeypatch):
    from ailab_platform.agent.loop import _Turn
    from ailab_platform.catalog import Catalog, ModelSpec
    from ailab_platform.model_policy import ModelPolicy

    catalog = Catalog([ModelSpec("m", "model", provider="mock", context_tokens=11000)])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: catalog)
    monkeypatch.setattr(ModelPolicy, "input_budget", staticmethod(lambda spec: 10000))
    platform = FakePlatform()
    model = ScriptedLLM([LLMResponse(content="已执行前三步，后台句柄abc；保留文件 /workspace/a") for _ in range(10)])
    loop = AgentLoop(platform, FakeBroker(), model)
    state = fresh_state()
    state["messages"] = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "original constraints"},
    ]
    state["user_message_index"] = 2
    for i in range(5):
        state["messages"].extend(
            [
                {"role": "assistant", "tool_calls": [{"id": str(i), "type": "function", "function": {"name": "exec", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": str(i), "content": "x" * 7600},
            ]
        )
    loop._states["run_1"], loop._steps["run_1"] = state, 0
    turn = _Turn({"prompt": "original constraints", "_attempt_id": "a", "_ledger_tokens": 0})
    turn.messages = state["messages"]
    asyncio.run(loop._compact_work_context("run_1", turn, model, "m", []))
    assert turn.messages[1]["content"] == "context"
    assert turn.messages[2] == {"role": "user", "content": "original constraints"}
    assert "本轮执行摘要" in turn.messages[3]["content"]
    assert "平台已确认交付" in turn.messages[4]["content"]
    assert [m["tool_call_id"] for m in turn.messages if m["role"] == "tool"] == ["3", "4"]
    assert platform.checkpoint and platform.reservations


def test_stalled_model_is_canceled_metered_and_paused(monkeypatch):
    monkeypatch.setattr(settings, "run_stall_seconds", 0.02)
    monkeypatch.setattr("ailab_platform.agent.loop.CANCEL_POLL_SECONDS", 0.005)
    canceled = []

    class Model(ScriptedLLM):
        async def chat(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                canceled.append(True)

    platform, broker = _run(Model([]))
    assert canceled and platform.settlements and platform.paused["status"] == "interrupted"
    assert not broker.leases and platform.checkpoint


@pytest.mark.parametrize("name", ["exec_wait", "exec_kill"])
def test_started_background_controls_can_replay_on_resume(name):
    class Broker(FakeBroker):
        controls = []

        async def exec(self, lease_id, command, timeout=None):
            self.controls.append(background_request(self, command)["action"])
            return {"exit_code": 0, "stdout": json.dumps({"ok": True, "handle": "a" * 32, "running": False, "exit_code": 0})}

    state = fresh_state()
    state["messages"] = [{"role": "user", "content": "检查后台命令"}]
    state["pending"] = [{"id": "c", "name": name, "arguments": {"handle": "a" * 32}, "ordinal": 0}]
    state["tool_log"]["0:0"] = {"name": name, "started": True}
    platform = FakePlatform()
    platform.checkpoint = {"step": 1, "state": state}
    broker = Broker()
    _run(ScriptedLLM([LLMResponse(content="已检查")]), platform, broker, attempt_id="next")
    assert broker.controls == [{"exec_wait": "wait", "exec_kill": "kill"}[name], "kill_all"]
    assert platform.checkpoint["state"]["tool_log"]["0:0"]["result"]["ok"]


def test_v1_checkpoint_resumes_with_hourly_note_after_pending_tool_pair():
    state = fresh_state()
    state["budget"] = {"window_id": "run_1", "active_seconds": 3600, "review_at": 3600, "review_progress": 1, "start_step": 0}
    state["progress_marks"] = ["old-file"]
    state["messages"] = [
        {"role": "user", "content": "继续检查"},
        {"role": "assistant", "tool_calls": [{"id": "c", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}]},
    ]
    state["pending"] = [{"id": "c", "name": "list_files", "arguments": {}, "ordinal": 0}]
    platform = FakePlatform()
    platform.checkpoint = {"step": 1, "state": state}
    model = ScriptedLLM([LLMResponse(content="检查完成")])
    _run(model, platform, attempt_id="retry")
    assert platform.statuses[-1][0] == "succeeded"
    messages = model.seen_messages[0]
    assert [message["role"] for message in messages] == ["system", "system", "user", "assistant", "tool", "user"]
    assert messages[2]["content"] == "继续检查"
    assert ("agent.context_boundary", {"reason": "rebuilt"}) in platform.events
    assert "对照计划" in messages[-1]["content"]
    assert "review_at" not in platform.checkpoint["state"]["budget"]
