"""The bash wait window: a checkpoint the model chooses, not a deadline that kills the command.

The Agent waits inside one tool call by issuing several bounded Broker calls; the sandbox's own
supervisor enforces the platform deadline whether or not anything is still polling.
"""

from __future__ import annotations

import asyncio
import json

from ailab_platform.adapters.runtime import UNCERTAIN_EXEC, fresh_state
from ailab_platform.agent import background_exec as module
from ailab_platform.agent.background_exec import background_exec, command_handle
from ailab_platform.agent.loop import AgentLoop, _Turn
from ailab_platform.llm import ToolCall
from tests.test_agent_loop import FakeBroker as LoopBroker
from tests.test_agent_loop import FakePlatform
from tests.test_background_exec import LocalBroker


class ScriptedBroker:
    """Answers each control call with the next scripted sandbox response."""

    def __init__(self, responses: list[dict], delay: float = 0.0) -> None:
        self.responses = list(responses)
        self.delay = delay
        self.commands: list[str] = []
        self.files: list[bytes] = []

    async def put_file(self, lease_id, path, data):
        self.files.append(data)
        return None

    async def exec(self, lease_id, command, timeout=None):
        self.commands.append(command)
        if self.delay:
            await asyncio.sleep(self.delay)
        payload = self.responses.pop(0)
        return {"exit_code": 0, "stdout": json.dumps(payload), "stderr": "", "duration_ms": 1}


def running(**fields) -> dict:
    return {"ok": True, "running": True, "handle": "a" * 32, "exit_code": None, "output_tail": "", "total_bytes": 0, **fields}


def test_one_tool_call_waits_through_several_sandbox_round_trips(monkeypatch):
    monkeypatch.setattr(module, "WAIT_CHUNK_SECONDS", 0.1)
    broker = ScriptedBroker([running()] * 40)

    started = asyncio.run(background_exec("bash", {"cmd": "sleep 30", "wait_seconds": 1}, "lease", broker))

    assert started["running"] and started["handle"] == "a" * 32
    assert 2 <= len(broker.commands) <= 40  # several bounded calls, one model turn
    assert started["wait_seconds"] == 1
    assert broker.responses  # the window ended before the scripted answers ran out


def test_the_window_end_is_a_checkpoint_not_a_failure(monkeypatch):
    monkeypatch.setattr(module, "WAIT_CHUNK_SECONDS", 0.1)
    broker = ScriptedBroker(
        [running(total_bytes=5, output_tail="first", idle_seconds=12.0, elapsed_seconds=1.0, remaining_seconds=99.0)] * 60
    )
    result = asyncio.run(background_exec("bash", {"cmd": "make", "wait_seconds": 1}, "lease", broker))
    assert result["ok"] is True and result["running"] is True and result["exit_code"] is None
    assert result["output"] == "first" and result["total_bytes"] == 5
    assert (result["elapsed_seconds"], result["remaining_seconds"], result["wait_seconds"]) == (1.0, 99.0, 1)
    assert "不是失败" in result["hint"] and "bash_kill" in result["hint"]


def test_a_long_silence_never_kills_the_command():
    broker = ScriptedBroker([running(idle_seconds=900.0)] * 20)
    result = asyncio.run(background_exec("bash_wait", {"handle": "a" * 32, "wait_seconds": 0}, "lease", broker))
    assert result["running"] is True
    assert all("kill" not in command for command in broker.commands)  # nothing but waits were sent


def test_completion_during_a_later_chunk_returns_the_final_result(monkeypatch):
    monkeypatch.setattr(module, "WAIT_CHUNK_SECONDS", 0.1)
    broker = ScriptedBroker(
        [
            running(total_bytes=3, output_tail="abc"),
            {
                "ok": True,
                "running": False,
                "handle": "a" * 32,
                "exit_code": 0,
                "output_tail": "done",
                "total_bytes": 7,
                "stdout": "abc\ndone\n",
                "stderr": "",
                "duration_ms": 900,
            },
        ]
    )
    result = asyncio.run(background_exec("bash_wait", {"handle": "a" * 32, "wait_seconds": 1}, "lease", broker))
    assert result["running"] is False and result["exit_code"] == 0 and result["ok"] is True
    assert result["stdout"] == "abc\ndone\n" and result["duration_ms"] == 900
    assert len(broker.commands) == 2  # completed during the second chunk, no extra waiting


def test_a_truncated_result_points_at_the_ring_buffer():
    broker = ScriptedBroker([running(total_bytes=4000, output_tail="tail", truncated=True, log_path="/tmp/ailab-background/x/log")] * 20)
    result = asyncio.run(background_exec("bash_wait", {"handle": "a" * 32, "wait_seconds": 0}, "lease", broker))
    assert result["truncated"] is True and result["log_path"] == "/tmp/ailab-background/x/log"


def test_an_untruncated_result_carries_no_path():
    broker = ScriptedBroker([running(total_bytes=4, output_tail="tail")])
    result = asyncio.run(background_exec("bash_wait", {"handle": "a" * 32, "wait_seconds": 0}, "lease", broker))
    assert "log_path" not in result


def test_output_stays_bounded_across_chunks(monkeypatch):
    monkeypatch.setattr(module, "WAIT_CHUNK_SECONDS", 0.1)
    chunk = "x" * 8000
    broker = ScriptedBroker(
        [running(total_bytes=8000, output_tail=chunk), running(total_bytes=16000, output_tail=chunk)] + [running(total_bytes=16000)] * 40
    )
    result = asyncio.run(background_exec("bash_wait", {"handle": "a" * 32, "wait_seconds": 1}, "lease", broker))
    assert result["running"]
    assert len(result["output"]) <= 9200  # head + tail, never the sum of every chunk
    assert result["output"].startswith("x") and result["output"].endswith("x")


def test_a_rejected_control_call_does_not_claim_a_result():
    broker = ScriptedBroker([{"ok": False, "error": "后台命令并发数已达上限，请先等待或终止已有命令"}])
    result = asyncio.run(background_exec("bash", {"cmd": "true", "background": True}, "lease", broker))
    assert result["ok"] is False and "上限" in result["error"] and "exit_code" not in result


def test_command_handle_is_stable_per_call_and_content():
    first = command_handle("run", "3:1", "make all")
    assert first == command_handle("run", "3:1", "make all")
    assert len(first) == 32
    assert first != command_handle("run", "3:2", "make all")
    assert first != command_handle("run", "3:1", "make test")


def test_a_retried_start_waits_on_the_running_command_instead_of_launching_it_twice(tmp_path):
    broker = LocalBroker(tmp_path)
    handle = command_handle("run", "1:0", "append")

    def launched():
        # Start returns once the process is recorded, not once its shell has run a line, so the
        # recorded process (pid + start time) is what tells one launch from two.
        return {path.parent.name: path.read_text() for path in (tmp_path / "jobs").glob("*/meta")}

    async def scenario():
        command = {"cmd": "sleep 20", "background": True}
        first = await background_exec("bash", command, "lease", broker, handle=handle)
        before = launched()
        second = await background_exec("bash", command, "lease", broker, handle=handle)
        try:
            assert first["running"] and second["running"] and second["handle"] == handle
            assert len(before) == 1 and launched() == before
        finally:
            await background_exec("bash_kill", {"handle": handle}, "lease", broker)

    asyncio.run(scenario())


def test_the_platform_deadline_kills_the_command_and_is_not_reset_by_waiting(tmp_path):
    broker = LocalBroker(tmp_path)

    async def scenario():
        started = await background_exec("bash", {"cmd": "echo begin; sleep 30", "background": True}, "lease", broker, max_seconds=1.5)
        handle = started["handle"]
        await asyncio.sleep(0.6)
        first = await background_exec("bash_wait", {"handle": handle, "wait_seconds": 0}, "lease", broker)
        assert first["running"] and 0 < first["remaining_seconds"] <= 1.5
        await asyncio.sleep(0.6)
        second = await background_exec("bash_wait", {"handle": handle, "wait_seconds": 0}, "lease", broker)
        # Waiting again never moves the deadline: what is left only shrinks.
        assert second["remaining_seconds"] < first["remaining_seconds"]
        final = await background_exec("bash_wait", {"handle": handle, "wait_seconds": 5}, "lease", broker)
        assert final["running"] is False and final["exit_code"] == -9 and final["ok"] is False
        assert final["remaining_seconds"] == 0 and "平台期限" in final["error"]
        assert "begin" in final["stdout"]  # bounded output is kept

    asyncio.run(scenario())


def test_a_caller_kill_is_reported_as_such(tmp_path):
    broker = LocalBroker(tmp_path)

    async def scenario():
        started = await background_exec("bash", {"cmd": "sleep 30", "background": True}, "lease", broker)
        killed = await background_exec("bash_kill", {"handle": started["handle"]}, "lease", broker)
        assert killed["exit_code"] == -9 and killed["running"] is False

    asyncio.run(scenario())


def test_resume_waits_on_the_same_command_instead_of_starting_it(monkeypatch):
    loop = AgentLoop(FakePlatform(), LoopBroker())
    loop._states["r"] = fresh_state()
    loop._states["r"]["tool_log"]["2:0"] = {"name": "bash", "started": True, "handle": "b" * 32}
    seen = {}

    async def fake_run_tool(turn, run_id, call, step, invocation="", event_name=None, announce=True):
        seen.update(name=call.name, arguments=call.arguments, invocation=invocation)
        return {"ok": True, "running": True, "exit_code": None, "handle": call.arguments["handle"], "wait_seconds": 1}

    monkeypatch.setattr(loop, "_run_tool", fake_run_tool)
    result = asyncio.run(loop._durable_tool(_Turn({"options": {}}), "r", ToolCall("c", "bash", {"cmd": "make"}), 2, "2:0"))
    assert seen["name"] == "bash_wait" and seen["arguments"] == {"handle": "b" * 32}
    assert result["resumed"] is True and result["running"] is True


def test_resume_reports_a_lost_command_without_running_it_again(monkeypatch):
    loop = AgentLoop(FakePlatform(), LoopBroker())
    loop._states["r"] = fresh_state()
    loop._states["r"]["tool_log"]["2:0"] = {"name": "bash", "started": True, "handle": "b" * 32}

    async def gone(turn, run_id, call, step, invocation="", event_name=None, announce=True):
        return {
            "ok": False,
            "running": False,
            "lost": True,
            "error": "后台命令已不存在；租约恢复后请检查工作区，勿直接重复启动",
        }

    monkeypatch.setattr(loop, "_run_tool", gone)
    result = asyncio.run(loop._durable_tool(_Turn({"options": {}}), "r", ToolCall("c", "bash", {"cmd": "make"}), 2, "2:0"))
    assert result["interrupted"] is True and result["error"] == UNCERTAIN_EXEC
    assert result["handle"] == "b" * 32


def test_a_finished_failing_command_keeps_its_result_across_a_restart(monkeypatch):
    """exit 1 is a result the model must see; only a lost execution is uncertain."""
    loop = AgentLoop(FakePlatform(), LoopBroker())
    loop._states["r"] = fresh_state()
    loop._states["r"]["tool_log"]["2:0"] = {"name": "bash", "started": True, "handle": "b" * 32}

    async def finished(turn, run_id, call, step, invocation="", event_name=None, announce=True):
        return {"ok": False, "exit_code": 1, "running": False, "stdout": "", "stderr": "boom\n", "error": "Command exited with code 1"}

    monkeypatch.setattr(loop, "_run_tool", finished)
    result = asyncio.run(loop._durable_tool(_Turn({"options": {}}), "r", ToolCall("c", "bash", {"command": "make"}), 2, "2:0"))
    assert result["exit_code"] == 1 and result["stderr"] == "boom\n"
    assert result["resumed"] is True and "interrupted" not in result


def test_a_command_whose_state_was_lost_stays_uncertain(monkeypatch):
    loop = AgentLoop(FakePlatform(), LoopBroker())
    loop._states["r"] = fresh_state()
    loop._states["r"]["tool_log"]["2:0"] = {"name": "bash", "started": True, "handle": "b" * 32}

    async def state_lost(turn, run_id, call, step, invocation="", event_name=None, announce=True):
        return {
            "ok": False,
            "lost": True,
            "exit_code": -1,
            "running": False,
            "stdout": "",
            "stderr": "",
            "error": "后台进程退出状态丢失，请先检查文件",
        }

    monkeypatch.setattr(loop, "_run_tool", state_lost)
    result = asyncio.run(loop._durable_tool(_Turn({"options": {}}), "r", ToolCall("c", "bash", {"command": "make"}), 2, "2:0"))
    assert result["interrupted"] is True and result["error"] == UNCERTAIN_EXEC


def test_a_resumed_wait_keeps_the_original_tool_name_for_the_ui(monkeypatch):
    """Portal closes the running step by name; a renamed event leaves the ``bash`` step spinning forever."""
    loop = AgentLoop(FakePlatform(), LoopBroker())
    loop._states["r"] = fresh_state()
    loop._states["r"]["tool_log"]["2:0"] = {"name": "bash", "started": True, "handle": "b" * 32}
    seen = {}

    async def fake_run_tool(turn, run_id, call, step, invocation="", event_name=None, announce=True):
        seen.update(call=call.name, event_name=event_name, announce=announce)
        return {"ok": True, "running": True, "exit_code": None, "handle": call.arguments["handle"]}

    monkeypatch.setattr(loop, "_run_tool", fake_run_tool)
    asyncio.run(loop._durable_tool(_Turn({"options": {}}), "r", ToolCall("c", "bash", {"command": "make"}), 2, "2:0"))
    assert seen == {"call": "bash_wait", "event_name": "bash", "announce": False}


def test_a_started_command_records_its_handle_for_a_resumed_run(monkeypatch):
    loop = AgentLoop(FakePlatform(), LoopBroker())
    loop._states["r"] = fresh_state()

    async def started(turn, run_id, call, step, invocation="", event_name=None, announce=True):
        return {"ok": True, "running": True, "handle": "pending", "hint": "…"}

    monkeypatch.setattr(loop, "_run_tool", started)
    asyncio.run(loop._durable_tool(_Turn({"options": {}}), "r", ToolCall("c", "bash", {"cmd": "make all"}), 3, "3:0"))
    record = loop._states["r"]["tool_log"]["3:0"]
    assert record["handle"] == command_handle("r", "3:0", "make all")


def test_a_kill_that_found_the_command_reports_success():
    """The model asked for it to stop; reporting ok:false makes the UI show a failure and invites a retry."""
    broker = ScriptedBroker(
        [
            {
                "ok": True,
                "running": False,
                "handle": "a" * 32,
                "exit_code": -9,
                "killed_by": "caller",
                "stdout": "",
                "stderr": "",
            }
        ]
    )
    result = asyncio.run(background_exec("bash_kill", {"handle": "a" * 32}, "lease", broker))
    assert result["ok"] is True and result["killed_by"] == "caller"
    assert result["running"] is False and result["exit_code"] == -9 and "error" not in result


def test_a_later_wait_on_a_killed_command_says_why_it_ended():
    broker = ScriptedBroker([{"ok": True, "running": False, "handle": "a" * 32, "exit_code": -9, "killed_by": "caller"}])
    result = asyncio.run(background_exec("bash_wait", {"handle": "a" * 32, "wait_seconds": 0}, "lease", broker))
    assert result["ok"] is False and result["error"] == "命令已被终止"


def test_a_retired_timeout_argument_is_ignored_with_a_note():
    broker = ScriptedBroker([{**running(), "running": False, "exit_code": 0, "stdout": "ok\n"}])
    result = asyncio.run(background_exec("bash", {"command": "true", "timeout": 60, "wait_seconds": 1}, "lease", broker))
    assert result["ok"] is True and result["exit_code"] == 0
    assert "timeout 已忽略" in result["note"]


def test_every_finished_command_has_the_same_shape(monkeypatch):
    monkeypatch.setattr(module, "WAIT_CHUNK_SECONDS", 0.1)
    finished_early = ScriptedBroker([{"ok": True, "running": False, "exit_code": 0, "stdout": "hi\n", "stderr": "", "duration_ms": 5}])
    finished_later = ScriptedBroker(
        [running(total_bytes=3, output_tail="abc"), {"ok": True, "running": False, "exit_code": 2, "stdout": "out\n", "stderr": "err\n"}]
    )
    early = asyncio.run(background_exec("bash", {"command": "true", "wait_seconds": 1}, "lease", finished_early))
    late = asyncio.run(background_exec("bash_wait", {"handle": "a" * 32, "wait_seconds": 1}, "lease", finished_later))
    for result in (early, late):
        assert result["running"] is False
        assert {"ok", "exit_code", "stdout"} <= set(result)
        assert "output_tail" not in result
    assert early["stderr"] == "" and early["duration_ms"] == 5
    assert late["stderr"] == "err\n" and late["error"] == "Command exited with code 2"


def test_a_gap_between_what_was_read_and_what_arrived_is_marked(monkeypatch):
    monkeypatch.setattr(module, "WAIT_CHUNK_SECONDS", 0.1)
    broker = ScriptedBroker([running(total_bytes=9000, output_tail="tail")] * 40)
    result = asyncio.run(background_exec("bash_wait", {"handle": "a" * 32, "wait_seconds": 1}, "lease", broker))
    assert result["running"] and "...[elided 8996 bytes]..." in result["output"] and result["output"].endswith("tail")


def test_the_gap_uses_the_helpers_read_start_not_a_redecoded_length(monkeypatch):
    """Invalid UTF-8 becomes one replacement character (3 bytes), so a byte-length gap can come out wrong."""
    monkeypatch.setattr(module, "WAIT_CHUNK_SECONDS", 0.1)
    broker = ScriptedBroker([running(total_bytes=9000, output_tail="\ufffd", **{"from": 8999})] * 40)
    result = asyncio.run(background_exec("bash_wait", {"handle": "a" * 32, "wait_seconds": 1}, "lease", broker))
    assert "...[elided 8999 bytes]..." in result["output"]


def test_the_command_may_be_named_command_or_cmd():
    for arguments in ({"command": "make"}, {"cmd": "make"}):
        broker = ScriptedBroker([{"ok": True, "running": False, "exit_code": 0, "stdout": ""}])
        asyncio.run(background_exec("bash", {**arguments, "wait_seconds": 1}, "lease", broker))
        assert b'"cmd": "make"' in broker.files[-1]


def test_a_resumed_command_does_not_open_a_second_ui_step(monkeypatch):
    """The crash announced the call; announcing again would open a step the result never closes."""
    from ailab_platform.agent import loop as loop_module
    from ailab_platform.agent.budgets import WorkBudget
    from ailab_platform.api import process_view

    loop = AgentLoop(FakePlatform(), LoopBroker())
    loop._states["r"] = fresh_state()
    loop._states["r"]["tool_log"]["2:0"] = {"name": "bash", "started": True, "handle": "b" * 32}
    loop._budgets["r"] = WorkBudget(loop._states["r"], "job")

    async def still_running(name, args, lease, broker, ctx):
        return {"ok": True, "running": True, "exit_code": None, "handle": "b" * 32, "output": ""}

    monkeypatch.setattr(loop_module, "execute_tool", still_running)
    crash = [("agent.tool_call", {"step": 2, "name": "bash", "arguments": {"command": "make"}})]
    asyncio.run(loop._durable_tool(_Turn({"options": {}}), "r", ToolCall("c", "bash", {"command": "make"}), 2, "2:0"))

    items: list[dict] = []
    for type_, payload in [*crash, *loop.platform.events]:
        process_view.reduce_event(items, type_, payload)
    steps = [step for item in items if item["kind"] == "steps" for step in item["steps"] if step["name"] == "bash"]
    assert len(steps) == 1
    assert steps[0]["status"] == "note"  # still running, and the step the crash opened is the one updated


def test_a_lost_exit_state_keeps_the_sandbox_message_and_says_lost():
    """A SIGHUP-killed command also reports -1, so -1 alone must not mean "no result"."""
    broker = ScriptedBroker(
        [
            {
                "ok": False,
                "running": False,
                "handle": "a" * 32,
                "exit_code": -1,
                "lost": True,
                "error": "后台进程退出状态丢失，请先检查文件",
            }
        ]
    )
    result = asyncio.run(background_exec("bash_wait", {"handle": "a" * 32, "wait_seconds": 0}, "lease", broker))
    assert result["running"] is False and result["exit_code"] == -1
    assert result["lost"] is True and result["error"] == "后台进程退出状态丢失，请先检查文件"


def test_a_killed_by_signal_command_is_not_reported_as_lost():
    broker = ScriptedBroker([{"ok": False, "running": False, "handle": "a" * 32, "exit_code": -1, "killed_by": "deadline"}])
    result = asyncio.run(background_exec("bash_wait", {"handle": "a" * 32, "wait_seconds": 0}, "lease", broker))
    assert not result.get("lost") and result["killed_by"] == "deadline"
