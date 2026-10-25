"""SIGKILL in a long-running background start recovers under a short durable lease."""

import asyncio
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from ailab_platform.agent.loop import AgentLoop
from ailab_platform.api.main import app
from ailab_platform.api.preview_dispatch import dispatch_preview_jobs
from ailab_platform.clients import AsyncPlatformClient
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.llm import LLMResponse
from ailab_platform.models import OutboxMessage, Run
from ailab_platform.queue import get_queue
from tests.faults.test_transactional_jobs import api  # noqa: F401
from tests.test_agent_loop import FakeBroker, ScriptedLLM
from tests.test_ledger_protocol import SERVICE
from tests.test_transactional_jobs import action, claim, start


async def platform_client():
    platform = AsyncPlatformClient(base_url="http://test", token="test-service")
    await platform._http.aclose()
    platform._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    return platform


def test_killed_long_run_reclaims_checkpoint_without_repeating_delivery_or_background_start(api, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "run_timeout_seconds", 86400)
    rid = start(api)
    marker = tmp_path / "operations"
    code = """
import asyncio, os, signal, sys
from pathlib import Path
from ailab_platform.agent.loop import AgentLoop
from ailab_platform.llm import LLMResponse, ToolCall
from ailab_platform.runtime_context import attempt_id_var
from tests.faults.test_long_run_recovery import platform_client
from tests.test_agent_loop import FakeBroker, ScriptedLLM
rid, marker = sys.argv[1:]
class Broker(FakeBroker):
    async def commit(self, *args, **kwargs):
        Path(marker).write_text("delivered\\n")
        return {"file_id": "confirmed", "filename": "a.txt", "size": 1}
    async def exec(self, *args, **kwargs):
        with Path(marker).open("a") as out:
            out.write("background_started\\n")
        os.kill(os.getpid(), signal.SIGKILL)
    async def snapshot(self, *args):
        return {"key": f"workspaces/{rid}/snapshot.tgz", "size": 1}
async def run():
    platform = await platform_client()
    owned = await platform.job_action(rid, "claim", consumer="killed-agent")
    attempt_id_var.set(owned["token"])
    llm = ScriptedLLM([LLMResponse(tool_calls=[
        ToolCall("deliver", "deliver_file", {"path": "/workspace/outputs/a.txt"}),
        ToolCall("start", "exec", {"cmd": "slow build", "background": True})])])
    await AgentLoop(platform, Broker(), llm).process(rid, attempt_id=owned["token"])
asyncio.run(run())
"""
    proc = subprocess.run([sys.executable, "-c", code, rid, str(marker)], env=os.environ.copy(), capture_output=True, timeout=30)
    assert proc.returncode == -signal.SIGKILL, proc.stderr.decode()
    assert marker.read_text().splitlines() == ["delivered", "background_started"]
    with SessionLocal() as db:
        job = db.get(OutboxMessage, rid)
        old = job.claim_token
        remaining = (
            (job.claimed_until.astimezone(UTC) if job.claimed_until.tzinfo else job.claimed_until.replace(tzinfo=UTC)) - datetime.now(UTC)
        ).total_seconds()
        assert 250 < remaining <= settings.job_lease_seconds
        job.claimed_until = datetime.now(UTC) - timedelta(seconds=1)
        job.next_attempt_at = job.claimed_until
        db.commit()
    assert action(api, rid, "renew", old).status_code == 409
    assert dispatch_preview_jobs(rid) == 1
    assert any(p["job_id"] == rid for p in get_queue().outstanding(f"{settings.run_queue}:work"))
    second = claim(api, rid)
    assert second["attempt"] == 2
    assert action(api, rid, "ack", old).status_code == 409

    class RecoveredBroker(FakeBroker):
        async def commit(self, *args, **kwargs):
            raise AssertionError("confirmed delivery repeated")

        async def exec(self, *args, **kwargs):
            raise AssertionError("background start repeated")

        async def snapshot(self, *args):
            return {"key": f"workspaces/{rid}/snapshot.tgz", "size": 1}

    async def recover():
        platform = await platform_client()
        try:
            await AgentLoop(platform, RecoveredBroker(), ScriptedLLM([LLMResponse(content="已从检查点继续")])).process(
                rid, attempt=2, attempt_id=second["token"]
            )
        finally:
            await platform.aclose()

    asyncio.run(recover())
    with SessionLocal() as db:
        assert db.get(Run, rid).status == "succeeded"
    checkpoint = api.get(f"/internal/runs/{rid}/checkpoint", headers=SERVICE).json()
    assert checkpoint["state"]["tool_log"]["0:1"]["result"]["interrupted"] is True
    assert len(checkpoint["state"]["artifacts"]) == 1
    assert Path(marker).read_text().splitlines() == ["delivered", "background_started"]
