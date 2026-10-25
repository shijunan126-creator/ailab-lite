"""Real Linux PID namespace stress test; no host-process substitute for this gate."""

import asyncio
import json

from ailab_platform.agent.background_exec import background_exec
from ailab_platform.config import settings
from ailab_platform.sandbox.base import SandboxSpec
from tests.contracts.test_sandbox_lifecycle import provider  # noqa: F401


def test_finished_supervisors_do_not_exhaust_pid_namespace(provider):  # noqa: F811
    from uuid import uuid4

    import pytest

    if provider.name == "mock":
        pytest.skip("requires a real Linux PID namespace; host helper coverage is in test_background_exec")
    ref = provider.acquire(
        "exec-reaping-" + uuid4().hex, settings.sandbox_image, ttl_seconds=300, spec=SandboxSpec(image=settings.sandbox_image, ttl=300)
    )

    class Broker:
        async def put_file(self, lease, path, data):
            await asyncio.to_thread(provider.put_file, lease, path, data)

        async def exec(self, lease, command, timeout=None):
            result = await asyncio.to_thread(provider.exec, lease, command, timeout or 30)
            return {"exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr}

    async def exercise():
        broker = Broker()
        for _ in range(270):
            result = await background_exec("exec", {"cmd": "true", "yield_seconds": 1}, ref, broker)
            assert result["exit_code"] == 0, result
        program = (
            "from pathlib import Path;import json;"
            "print(json.dumps([p.read_text().rsplit(chr(41),1)[1].split()[0] "
            "for p in Path('/proc').glob('[0-9]*/stat')]))"
        )
        import shlex

        probe = await broker.exec(ref, "python3 -c " + shlex.quote(program), 10)
        states = json.loads(probe["stdout"])
        assert states.count("Z") < 3, states
        assert (await background_exec("exec_kill_all", {}, ref, broker))["ok"]
        assert (await broker.exec(ref, "tar -czf /tmp/probe.tgz -C /workspace .", 10))["exit_code"] == 0

    try:
        asyncio.run(exercise())
    finally:
        provider.release(ref)
