"""Exercise the actual detached helper through a local exec transport (Linux / macOS)."""

import asyncio
import base64
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

    async def exec(self, lease, command, timeout=None):
        self.commands.append(command)
        args = shlex.split(command)
        source = Path(__file__).parents[1] / "ailab_platform/agent/background_helper.py"
        original = source.read_bytes()
        changed = original.replace(b'Path("/tmp/ailab-background")', f"Path({str(self.root)!r})".encode())
        args[0] = sys.executable
        args[2] = args[2].replace(base64.b64encode(original).decode(), base64.b64encode(changed).decode())
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
            assert waited["running"] and waited["output"] == "first"
            again = await execute_tool(
                "exec_wait", {"handle": start["handle"], "seconds": 0, "offset": waited["total_bytes"]}, "lease", broker
            )
            assert again["output"] == ""
        finally:
            killed = await execute_tool("exec_kill", {"handle": start["handle"]}, "lease", broker)
        assert not killed["running"] and killed["exit_code"] == -9
        assert (await execute_tool("exec_kill", {"handle": start["handle"]}, "lease", broker))["exit_code"] == -9
        command = shlex.join([sys.executable, "-c", "print('a'*20000); print('TAIL')"])
        start = await execute_tool("exec", {"cmd": command, "background": True}, "lease", broker)
        waited = await execute_tool("exec_wait", {"handle": start["handle"], "seconds": 5}, "lease", broker)
        assert waited["exit_code"] == 0 and waited["total_bytes"] > 20000
        assert waited["truncated"] and waited["output"].endswith("TAIL\n") and len(waited["output"]) <= 4000
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
