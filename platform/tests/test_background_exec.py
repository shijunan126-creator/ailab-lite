"""Exercise the actual detached helper through a local exec transport (Linux / macOS)."""

import asyncio
import os
import shlex
import sys
from pathlib import Path

import pytest

from ailab_platform.agent.background_exec import background_exec
from ailab_platform.agent.tools import execute_tool, tools_for_chat, tools_for_turn
from ailab_platform.config import settings


class LocalBroker:
    def __init__(self, tmp_path):
        self.root = tmp_path / "jobs"
        self.commands = []
        self.source_transform = lambda source: source

    async def exec(self, lease, command, timeout=None):
        self.commands.append(command)
        args = shlex.split(command)
        source = Path(__file__).parents[1] / "ailab_platform/agent/background_helper.py"
        original = source.read_bytes()
        changed = original.replace(b'Path("/tmp/ailab-background")', f"Path({str(self.root)!r})".encode())
        changed = self.source_transform(changed)
        args[0] = sys.executable
        args[2] = args[2].replace(repr(original.decode()), repr(changed.decode()))
        child = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(child.communicate(), timeout or 30)
        return {"exit_code": child.returncode, "stdout": out.decode(), "stderr": err.decode()}


def test_background_tools_start_wait_kill_and_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "exec_background_max_jobs", 1)
    monkeypatch.setattr(settings, "exec_background_log_bytes", 4096)
    broker = LocalBroker(tmp_path)

    async def scenario():
        start = await execute_tool("exec", {"cmd": "printf 'first'; sleep 10", "background": True}, "lease", broker)
        assert start["ok"] and start["running"] and len(start["handle"]) == 32
        try:
            full = await execute_tool("exec", {"cmd": "true", "background": True}, "lease", broker)
            assert not full["ok"] and "上限" in full["error"]
            waited = await execute_tool("exec_wait", {"handle": start["handle"], "seconds": 1}, "lease", broker)
            assert waited["running"] and waited["output_tail"] == "first"
            again = await execute_tool(
                "exec_wait", {"handle": start["handle"], "seconds": 0, "offset": waited["total_bytes"]}, "lease", broker
            )
            assert again["output_tail"] == ""
        finally:
            killed = await execute_tool("exec_kill", {"handle": start["handle"]}, "lease", broker)
        assert not killed["running"] and killed["exit_code"] == -9
        assert (await execute_tool("exec_kill", {"handle": start["handle"]}, "lease", broker))["exit_code"] == -9
        command = shlex.join([sys.executable, "-c", "print('a'*20000); print('TAIL')"])
        start = await execute_tool("exec", {"cmd": command, "background": True}, "lease", broker)
        waited = await execute_tool("exec_wait", {"handle": start["handle"], "seconds": 5}, "lease", broker)
        assert waited["exit_code"] == 0 and waited["total_bytes"] > 20000
        assert waited["truncated"] and waited["output_tail"].endswith("TAIL\n") and len(waited["output_tail"]) <= 4000
        assert sum(p.stat().st_size for p in broker.root.glob("*/log")) <= 4096

    asyncio.run(scenario())


@pytest.mark.parametrize("args", [{"handle": "../bad"}, {"handle": "a" * 32, "seconds": 601}, {"handle": "a" * 32, "offset": -1}])
def test_background_controls_validate_before_exec(tmp_path, args):
    broker = LocalBroker(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(background_exec("exec_wait", args, "lease", broker))
    assert broker.commands == []


def test_missing_or_replaced_lease_never_signals_an_unrelated_pid(tmp_path):
    broker = LocalBroker(tmp_path)
    assert not asyncio.run(background_exec("exec_kill", {"handle": "a" * 32}, "lease", broker))["ok"]
    import json

    directory = broker.root / ("b" * 32)
    directory.mkdir()
    (directory / "meta").write_text(json.dumps({"pid": os.getpid(), "starttime": "wrong", "log_bytes": 4096}))
    result = asyncio.run(background_exec("exec_kill", {"handle": "b" * 32}, "lease", broker))
    assert result["exit_code"] == -9  # this test process remains alive


def test_new_tools_only_in_work_mode():
    def names(tools):
        return {t["function"]["name"] for t in tools}

    assert {"exec_wait", "exec_kill"} <= names(tools_for_turn())
    assert not {"exec", "exec_wait", "exec_kill"} & names(tools_for_chat())


def test_short_exec_single_roundtrip_preserves_streams_and_exit(tmp_path):
    broker = LocalBroker(tmp_path)
    result = asyncio.run(execute_tool("exec", {"cmd": "printf out; printf err >&2; exit 7"}, "lease", broker))
    assert len(broker.commands) == 1
    assert result == {"ok": False, "exit_code": 7, "stdout": "out", "stderr": "err", "duration_ms": result["duration_ms"]}


def test_unified_exec_yields_waits_and_reports_idle(tmp_path):
    broker = LocalBroker(tmp_path)

    async def scenario():
        result = await execute_tool("exec", {"cmd": "printf first; sleep 2; printf last", "yield_seconds": 1}, "lease", broker)
        assert len(broker.commands) == 1 and result["running"]
        assert result["output_tail"] == "first" and result["total_bytes"] == 5
        assert result["idle_seconds"] >= 0.5
        again = await execute_tool("exec_wait", {"handle": result["handle"], "seconds": 0}, "lease", broker)
        assert again["idle_seconds"] >= result["idle_seconds"]
        completed = await execute_tool("exec_wait", {"handle": result["handle"], "seconds": 300, "offset": 5}, "lease", broker)
        assert completed["exit_code"] == 0 and not completed["running"]
        assert completed["output_tail"] == "last" and completed["total_bytes"] == 9
        assert completed["idle_seconds"] < 1

    asyncio.run(scenario())


def test_pruning_keeps_running_and_recent_completed_handles(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(settings, "exec_background_max_jobs", 3)
    broker = LocalBroker(tmp_path)

    async def scenario():
        active = await execute_tool("exec", {"cmd": "sleep 30", "background": True}, "lease", broker)
        try:
            finished = []
            for _ in range(2):
                started = await execute_tool("exec", {"cmd": "true", "background": True}, "lease", broker)
                finished.append(started["handle"])
                await execute_tool("exec_wait", {"handle": started["handle"], "seconds": 2}, "lease", broker)
            latest = await execute_tool("exec", {"cmd": "true", "background": True}, "lease", broker)
            assert (broker.root / active["handle"]).exists()
            assert not (broker.root / finished[0]).exists()
            assert (broker.root / finished[1]).exists()
            assert (broker.root / latest["handle"]).exists()
            assert len(list(broker.root.glob("*/meta"))) == 3
        finally:
            await execute_tool("exec_kill", {"handle": active["handle"]}, "lease", broker)
        assert json.loads((broker.root / active["handle"] / "exit").read_text())["exit_code"] == -9

    asyncio.run(scenario())


@pytest.mark.parametrize("legacy_registry", [False, True])
def test_pause_kills_jobs_before_snapshot(tmp_path, legacy_registry):
    from ailab_platform.adapters.runtime import fresh_state
    from ailab_platform.agent.budgets import WorkBudget
    from ailab_platform.agent.loop import AgentLoop, _Turn
    from tests.test_agent_loop import FakePlatform

    broker = LocalBroker(tmp_path)
    platform = FakePlatform()
    loop = AgentLoop(platform, broker)
    state = fresh_state()
    loop._states["r"], loop._steps["r"] = state, 0
    loop._budgets["r"] = WorkBudget(state, "j")
    turn = _Turn({})
    turn.lease_id = "lease"
    order = []

    async def snapshot(*args, **kwargs):
        status = await execute_tool("exec_wait", {"handle": handle[0], "seconds": 0}, "lease", broker)
        assert not status["running"] and status["exit_code"] == -9
        order.append("snapshot")

    loop._workspace_checkpoint = snapshot
    handle = []

    async def scenario():
        start = await execute_tool("exec", {"cmd": "sleep 30", "background": True}, "lease", broker)
        handle.append(start["handle"])
        if not legacy_registry:
            state["background_jobs"] = ["f" * 32] * settings.exec_background_max_jobs + handle[:]
        try:
            await loop._budget_pause("r", turn, "测试暂停")
            assert state["background_jobs"] == []
        finally:
            await execute_tool("exec_kill", {"handle": start["handle"]}, "lease", broker)

    asyncio.run(scenario())
    assert order == ["snapshot"]


@pytest.mark.parametrize("args", [{"cmd": "true", "yield_seconds": 121}, {"cmd": "true", "yield_seconds": True}])
def test_invalid_yield_is_rejected_before_start(tmp_path, args):
    from ailab_platform.agent.tools import ToolError

    broker = LocalBroker(tmp_path)
    with pytest.raises(ToolError):
        asyncio.run(execute_tool("exec", args, "lease", broker))
    assert not broker.commands


def test_timeout_alias_and_legacy_settings_are_compatible(tmp_path, monkeypatch):
    from ailab_platform.config import Settings

    monkeypatch.setenv("AILAB_RUN_SOFT_BUDGET_SECONDS", "old-value-is-ignored")
    monkeypatch.setenv("AILAB_RUN_REVIEW_INTERVAL_SECONDS", "ignored")
    assert Settings(_env_file=None).run_max_seconds == 14400
    broker = LocalBroker(tmp_path)
    result = asyncio.run(execute_tool("exec", {"cmd": "printf ok", "timeout_seconds": 1}, "lease", broker))
    assert result["stdout"] == "ok" and len(broker.commands) == 1


def test_large_short_output_survives_provider_control_transport(tmp_path, monkeypatch):
    from ailab_platform.sandbox.mock import MockSandbox

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    provider = MockSandbox()
    lease = provider.acquire("control-probe", "mock")

    class Broker:
        calls = 0

        async def exec(self, lease_id, command, timeout=None):
            self.calls += 1
            result = await asyncio.to_thread(provider.exec, lease_id, command, timeout)
            return {"exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr}

    broker = Broker()
    command = shlex.join([sys.executable, "-c", "import sys; print('中'*4500); sys.stderr.write('\\x00'*4500)"])
    try:
        result = asyncio.run(execute_tool("exec", {"cmd": command}, lease, broker))
        assert broker.calls == 1 and result["ok"]
        assert result["stdout"] == "中" * 4500 + "\n"
        assert result["stderr"] == "\x00" * 4500
    finally:
        provider.release(lease)


def test_command_lifetime_is_clamped_to_remaining_budget(tmp_path):
    broker = LocalBroker(tmp_path)

    async def scenario():
        result = await background_exec("exec", {"cmd": "sleep 20", "background": True}, "lease", broker, max_seconds=0.5)
        completed = await background_exec("exec_wait", {"handle": result["handle"], "seconds": 2}, "lease", broker)
        assert completed["exit_code"] == -9 and not completed["running"]

    asyncio.run(scenario())


def test_legacy_immediate_yield_preserves_output_if_command_already_exited(tmp_path):
    broker = LocalBroker(tmp_path)
    broker.source_transform = lambda source: source.replace(
        b"def process_starttime(pid):", b"def process_starttime(pid):\n    time.sleep(0.2)"
    )
    result = asyncio.run(execute_tool("exec", {"cmd": "printf quick", "background": True}, "lease", broker))
    assert len(broker.commands) == 1
    assert result["exit_code"] == 0 and result["stdout"] == "quick"


@pytest.mark.parametrize("character", ["中", "\x00", "😀"])
def test_foreground_keeps_head_and_failure_tail_with_cjk_transport(tmp_path, monkeypatch, character):
    from ailab_platform.sandbox.mock import MockSandbox

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    provider = MockSandbox()
    lease = provider.acquire("cjk-tail", "mock")

    class Broker:
        async def exec(self, lease_id, command, timeout=None):
            result = await asyncio.to_thread(provider.exec, lease_id, command, timeout)
            assert len(result.stdout.encode()) < 120000
            return {"exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr}

    command = shlex.join(
        [
            sys.executable,
            "-c",
            f"import sys; print('HEAD'+{character!r}*20000+'FAILURE'); sys.stderr.write('HEAD'+{character!r}*20000+'ERROR')",
        ]
    )
    try:
        result = asyncio.run(execute_tool("exec", {"cmd": command}, lease, Broker()))
        for stream, ending in [("stdout", "FAILURE\n"), ("stderr", "ERROR")]:
            assert result[stream].endswith(ending) and "elided" in result[stream]
            assert result[stream].startswith("HEAD") and len(result[stream]) >= 9000
            assert result[stream][-8000:] == (character * 20000 + ending)[-8000:]
    finally:
        provider.release(lease)


def test_finish_kills_legacy_registry_jobs_missing_from_checkpoint(tmp_path):
    from ailab_platform.agent.loop import AgentLoop
    from ailab_platform.llm import LLMResponse, ToolCall
    from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM

    class Broker(LocalBroker, FakeBroker):
        def __init__(self):
            LocalBroker.__init__(self, tmp_path)
            FakeBroker.__init__(self)

        async def snapshot(self, lease_id):
            status = await background_exec("exec_wait", {"handle": handle[0], "seconds": 0}, lease_id, self)
            assert not status["running"] and status["exit_code"] == -9
            return await FakeBroker.snapshot(self, lease_id)

    broker, handle = Broker(), []

    async def scenario():
        started = await background_exec("exec", {"cmd": "sleep 30", "background": True}, "lease_1", broker)
        handle.append(started["handle"])
        try:
            llm = ScriptedLLM([LLMResponse(tool_calls=[ToolCall("c", "list_files", {})]), LLMResponse(content="done")])
            platform = FakePlatform()
            await AgentLoop(platform, broker, llm).process("run_1", attempt_id="a")
            status = await background_exec("exec_wait", {"handle": handle[0], "seconds": 0}, "lease_1", broker)
            assert not status["running"] and status["exit_code"] == -9
            assert platform.statuses[-1][0] == "succeeded"
        finally:
            await background_exec("exec_kill", {"handle": handle[0]}, "lease_1", broker)

    asyncio.run(scenario())
