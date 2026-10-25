"""Real Linux PID namespace stress test; no host-process substitute for this gate."""

import asyncio
import json
import shlex
import time

from ailab_platform.agent.background_exec import background_exec
from ailab_platform.config import settings
from ailab_platform.sandbox.base import SandboxSpec
from tests.contracts.test_sandbox_lifecycle import provider  # noqa: F401
from tests.test_ledger_protocol import api  # noqa: F401


def test_finished_supervisors_do_not_exhaust_pid_namespace(provider):  # noqa: F811
    from uuid import uuid4

    import pytest

    if provider.name == "mock":
        pytest.skip("requires a real Linux PID namespace; host helper coverage is in test_background_exec")
    ref = provider.acquire(
        "exec-reaping-" + uuid4().hex, settings.sandbox_image, ttl_seconds=1200, spec=SandboxSpec(image=settings.sandbox_image, ttl=1200)
    )

    class Broker:
        async def put_file(self, lease, path, data):
            await asyncio.to_thread(provider.put_file, lease, path, data)

        async def exec(self, lease, command, timeout=None):
            result = await asyncio.to_thread(provider.exec, lease, command, timeout or 30)
            return {"exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr}

    async def exercise():
        broker = Broker()
        started = time.monotonic()
        maximum = 0
        for index in range(400):
            result = await background_exec("exec", {"cmd": "true", "yield_seconds": 1}, ref, broker)
            assert result["exit_code"] == 0, result
            if (index + 1) % 100 == 0:
                probe = await broker.exec(ref, "ps -eo stat=", 10)
                assert probe["exit_code"] == 0, probe
                states = probe["stdout"].split()
                maximum = max(maximum, len(states))
                zombies = sum(s.startswith("Z") for s in states)
                assert zombies < 3, states
                assert len(states) < 64, states
                print(f"{provider.name}/{settings.sandbox_runtime}: execs={index + 1} pids={len(states)} zombies={zombies}", flush=True)
        program = (
            "from pathlib import Path;import json;"
            "print(json.dumps([p.read_text().rsplit(chr(41),1)[1].split()[0] "
            "for p in Path('/proc').glob('[0-9]*/stat')]))"
        )
        probe = await broker.exec(ref, "python3 -c " + shlex.quote(program), 10)
        states = json.loads(probe["stdout"])
        assert states.count("Z") < 3, states
        assert (await background_exec("exec_kill_all", {}, ref, broker))["ok"]
        assert (await broker.exec(ref, "tar -czf /tmp/probe.tgz -C /workspace .", 10))["exit_code"] == 0
        print(f"400 execs elapsed={time.monotonic() - started:.3f}s max_sampled_pids={maximum}; tar/kill_all passed", flush=True)

    try:
        asyncio.run(exercise())
    finally:
        provider.release(ref)


def test_real_exec_wait_output_kill_tree_and_supervisor_loss(provider, monkeypatch):  # noqa: F811
    from uuid import uuid4

    import pytest

    if provider.name == "mock":
        pytest.skip("requires real Linux process isolation")
    monkeypatch.setattr(settings, "exec_background_max_jobs", 1)
    spec = SandboxSpec(image=settings.sandbox_image, ttl=300)
    ref = provider.acquire("exec-controls-" + uuid4().hex, spec.image, spec=spec)

    class Broker:
        async def put_file(self, lease, path, data):
            await asyncio.to_thread(provider.put_file, lease, path, data)

        async def exec(self, lease, command, timeout=None):
            value = await asyncio.to_thread(provider.exec, lease, command, timeout or 30)
            return {"exit_code": value.exit_code, "stdout": value.stdout, "stderr": value.stderr}

    async def exercise():
        broker = Broker()

        async def call(name, **args):
            return await background_exec(name, args, ref, broker)

        async def probe(program):
            value = await broker.exec(ref, "python3 -c " + shlex.quote(program), 10)
            assert value["exit_code"] == 0, value
            return value["stdout"].strip()

        silent = await call("exec", cmd="sleep 5; printf silent-done", yield_seconds=1)
        assert silent["running"] and silent["total_bytes"] == 0
        polls = 0
        while silent["running"]:
            silent = await call("exec_wait", handle=silent["handle"], seconds=1)
            polls += 1
            assert polls < 10
        assert silent["exit_code"] == 0 and "silent-done" in silent["output_tail"]
        noisy = await call("exec", cmd="python3 -c \"print('HEAD' + 'x'*20000 + 'TAIL')\"", yield_seconds=1)
        assert noisy["exit_code"] == 0 and noisy["stdout"].startswith("HEAD") and noisy["stdout"].endswith("TAIL\n")
        command = "printf cjk-ok #" + "中" * (20000 - len("printf cjk-ok #"))
        assert len(command) == 20000
        assert (await call("exec", cmd=command, yield_seconds=1))["stdout"] == "cjk-ok"

        tree = "sleep 120 & sleep 120 & wait"
        job = await call("exec", cmd=tree, yield_seconds=1)
        handle = job["handle"]
        meta = f"/tmp/ailab-background/{handle}/meta"
        pid = int(await probe(f"import json;print(json.load(open({meta!r}))['pid'])"))
        count_group = (
            "from pathlib import Path;"
            f"print(sum(1 for p in Path('/proc').glob('[0-9]*/stat') "
            f"if (s:=p.read_text().rsplit(')',1)[1].split())[2]==str({pid}) and s[0]!='Z'))"
        )
        before = int(await probe(count_group))
        assert before >= 3
        assert (await call("exec_kill", handle=handle))["exit_code"] == -9
        assert int(await probe(count_group)) == 0
        job = await call("exec", cmd=tree, yield_seconds=1)
        assert (await call("exec_kill_all"))["killed"] == 1
        assert (await call("exec_wait", handle=job["handle"], seconds=0))["exit_code"] == -9
        reaper = "/tmp/ailab-background/reaper"
        assert await probe(f"import json,os;os.kill(json.load(open({reaper!r}))['pid'],0);print('alive')") == "alive"

        # Kill only the supervisor, just as an OOM victim can disappear without writing exit state.
        job = await call("exec", cmd="sleep 120", yield_seconds=1)
        meta = f"/tmp/ailab-background/{job['handle']}/meta"
        await probe(f"import json,os,signal;os.kill(json.load(open({meta!r}))['pid'],signal.SIGKILL)")
        failed = await call("exec_wait", handle=job["handle"], seconds=1)
        assert not failed["running"] and failed["exit_code"] == -1, failed
        assert (await call("exec", cmd="true", yield_seconds=1))["exit_code"] == 0  # the only slot is free
        # Orphaned descendants must also be removed on pause, even after the supervisor is gone.
        await call("exec_kill_all")
        states = await broker.exec(ref, "ps -eo pid,ppid,pgid,stat,args", 10)
        print(
            f"{provider.name}/{settings.sandbox_runtime}: silent_polls={polls}, noisy_chars={len(noisy['stdout'])}, "
            f"command_chars={len(command)}, kill_tree_members={before}, supervisor_exit={failed['exit_code']}\n{states['stdout']}",
            flush=True,
        )
        assert "sleep 120" not in states["stdout"], states

    try:
        asyncio.run(exercise())
    finally:
        provider.release(ref)
    assert provider.inspect(ref) is None


def test_real_polled_exec_snapshots_are_bounded_and_superseded_objects_deleted(provider, api, monkeypatch):  # noqa: F811
    import pytest

    from ailab_platform.agent.loop import AgentLoop
    from ailab_platform.llm import LLMResponse, ToolCall
    from ailab_platform.storage import get_storage
    from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM
    from tests.test_runtime_protocol import owned, save

    if provider.name == "mock":
        pytest.skip("requires real sandbox commands and snapshot tar")
    rid, _, headers = owned(api)
    spec = SandboxSpec(image=settings.sandbox_image, ttl=180)
    ref = provider.acquire("polled-" + rid, spec.image, spec=spec)
    storage = get_storage()
    monkeypatch.setattr("ailab_platform.agent.loop.WORKSPACE_DEBOUNCE_SECONDS", 0)

    class Platform(FakePlatform):
        async def put_checkpoint(self, run_id, payload):
            response = save(api, rid, headers, payload["expected_step"], payload["state"])
            assert response.status_code == 200, response.text
            return await super().put_checkpoint(run_id, payload)

    class Broker(FakeBroker):
        keys = []

        async def create_lease(self, *args, **kw):
            return {"lease_id": ref, "inputs": []}

        async def put_file(self, lease, path, data):
            await asyncio.to_thread(provider.put_file, lease, path, data)

        async def exec(self, lease, command, timeout=None):
            value = await asyncio.to_thread(provider.exec, lease, command, timeout or 30)
            return {"exit_code": value.exit_code, "stdout": value.stdout, "stderr": value.stderr}

        async def snapshot(self, lease):
            self.snapshots += 1
            key = f"workspaces/{rid}/{self.snapshots}.tgz"
            result = await self.exec(
                lease, "mkdir -p /workspace/.ailab; tar -czf /workspace/.ailab/polled.tgz --exclude=./.ailab -C /workspace .", 30
            )
            assert result["exit_code"] == 0, result
            archive = await asyncio.to_thread(provider.get_file, lease, "/workspace/.ailab/polled.tgz")
            storage.put_bytes(key, archive)
            self.keys.append(key)
            return {"key": key, "size": len(archive)}

    broker, platform = Broker(), Platform()

    class Model(ScriptedLLM):
        polls = 0

        async def chat(self, messages, *args, **kw):
            if not any(m["role"] == "tool" for m in messages):
                return LLMResponse(tool_calls=[ToolCall("start", "exec", {"cmd": "sleep 8; printf done", "yield_seconds": 1})])
            result = json.loads(messages[-1]["content"])
            if result.get("running"):
                assert broker.snapshots == 1  # initial exec snapshot only, never one per running poll
                self.polls += 1
                assert self.polls < 12
                return LLMResponse(tool_calls=[ToolCall("wait", "exec_wait", {"handle": result["handle"], "seconds": 1})])
            assert result["exit_code"] == 0
            return LLMResponse(content="done")

    model = Model([])
    try:
        asyncio.run(AgentLoop(platform, broker, model).process(rid, attempt_id="test"))
        assert platform.statuses[-1][0] == "succeeded", platform.statuses
        assert model.polls >= 2 and broker.snapshots == 2
        assert [key for key in broker.keys if storage.exists(key)] == [broker.keys[-1]]
        print(f"{provider.name}: polls={model.polls}, snapshots={broker.snapshots}, live_objects=1, superseded_deleted=1", flush=True)
    finally:
        provider.release(ref)
