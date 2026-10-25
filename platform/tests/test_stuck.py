"""Repetition is semantic, checkpointed and reset by actual user input."""

import json

import pytest

from ailab_platform.agent.stuck import STUCK_ERROR, detect_loop
from ailab_platform.llm import LLMResponse, ToolCall
from tests.test_agent_loop import FakePlatform, ScriptedLLM, _run


def log_for(items):
    return {f"{i}:0": {"name": name, "arguments": args, "result": result} for i, (name, args, result) in enumerate(items)}


@pytest.mark.parametrize(
    "items,reason",
    [
        ([("read_file", {"path": "/workspace/a"}, {"ok": True, "content": "same"})] * 4, "identical_call_result"),
        ([("exec", {"cmd": "false"}, {"ok": False, "error": str(i)}) for i in range(3)], "repeated_call_error"),
        ([("read_file", {"path": path}, {"ok": True}) for path in ("a", "b") * 6], "alternating_call_result"),
    ],
)
def test_patterns_and_user_boundary(items, reason):
    log = log_for(items)
    assert detect_loop(log) == reason
    assert detect_loop(log, len(items)) is None
    assert detect_loop(log_for(items[:-1])) is None


def test_timings_are_volatile_but_advancing_output_is_not():
    args = {"handle": "a" * 32, "seconds": 1}
    same = [("exec_wait", args, {"ok": True, "running": True, "total_bytes": 5, "idle_seconds": i}) for i in range(4)]
    assert detect_loop(log_for(same)) == "identical_call_result"
    advancing = [("exec_wait", args, {"ok": True, "running": True, "total_bytes": i, "output_tail": str(i)}) for i in range(20)]
    assert detect_loop(log_for(advancing)) is None


@pytest.mark.parametrize("error", [False, True])
def test_agent_nudges_once_then_pauses_and_persists_reason(error):
    name, args = ("read_file", {"path": "/workspace/missing"}) if error else ("list_files", {})
    model = ScriptedLLM([LLMResponse(tool_calls=[ToolCall(str(i), name, args)]) for i in range(10)])
    platform, _ = _run(model)
    events = [payload for topic, payload in platform.events if topic == "agent.budget"]
    assert [payload["kind"] for payload in events] == ["nudge", "paused"]
    assert platform.paused["error"] == STUCK_ERROR
    assert any("停止重复" in str(m.get("content")) for m in model.seen_messages[-1])
    assert len(model.seen_messages) == (4 if error else 5)
    assert platform.checkpoint["state"]["loop_nudge"]


def test_user_steer_resets_observations_and_nudge():
    class Platform(FakePlatform):
        sent = False

        async def claim_steers(self, run_id):
            self.sent = True
            return {"items": [{"id": "steer", "prompt": "换一个方法"}]}

    platform = Platform()

    # Directly exercise the steer seam with a complete durable prefix, independent of heartbeat timing.
    import asyncio

    from ailab_platform.adapters.runtime import fresh_state
    from ailab_platform.agent.loop import AgentLoop, _Turn

    state = fresh_state()
    state["tool_log"] = log_for([("list_files", {}, {"ok": True})] * 4)
    state["loop_nudge"] = {"at": 4, "reason": "identical_call_result"}
    loop = AgentLoop(platform, None)
    loop._states["run_1"], loop._steps["run_1"] = state, 0
    asyncio.run(loop._apply_steers("run_1", _Turn({}), pending=True))
    assert platform.sent and "loop_nudge" not in state
    assert detect_loop(state["tool_log"], state["loop_start"]) is None
    assert state["messages"][-1] == {"role": "user", "content": "换一个方法"}
    assert json.dumps(state)
