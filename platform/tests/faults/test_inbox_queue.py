"""Failure injection for queue serialization and claim/checkpoint crash recovery."""

import asyncio
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.loop import AgentLoop, _Turn
from ailab_platform.db import SessionLocal
from ailab_platform.models import OutboxMessage, QueuedMessage, RunEvent
from tests.test_inbox_queue import conversation, detail, enqueue, finish
from tests.test_ledger_protocol import SERVICE, api  # noqa: F401
from tests.test_transactional_jobs import action, claim


def test_duplicate_completion_dispatches_once(api):  # noqa: F811
    conv, run, headers = conversation(api)
    qid = enqueue(api, conv).json()["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: finish(api, run, headers), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    with SessionLocal() as db:
        row = db.get(QueuedMessage, qid)
        assert row.state == "started"
        assert db.scalar(select(func.count()).select_from(OutboxMessage).where(OutboxMessage.id == row.run_id)) == 1
        assert detail(api, conv)["active_run_id"] == row.run_id


def test_concurrent_post_and_completion_never_strands_accepted_message(api):  # noqa: F811
    for _ in range(8):
        conv, run, headers = conversation(api)
        with ThreadPoolExecutor(max_workers=2) as pool:
            post = pool.submit(enqueue, api, conv)
            done = pool.submit(finish, api, run, headers)
            queued, terminal = post.result(), done.result()
        assert terminal.status_code == 200
        assert queued.status_code in {201, 409}
        if queued.status_code == 201:
            with SessionLocal() as db:
                row = db.get(QueuedMessage, queued.json()["id"])
                assert row.state == "started" and row.run_id


def test_agent_sigkill_after_claim_before_checkpoint_replays_once(api):  # noqa: F811
    conv, run, headers = conversation(api)
    qid = enqueue(api, conv, steer=True, prompt="durable steer").json()["id"]
    # Separate process uses the real API handlers and Agent injection method. It is
    # SIGKILLed immediately after the claim HTTP transaction commits.
    code = """
import asyncio, os, signal, sys
from fastapi.testclient import TestClient
from ailab_platform.api.main import app
from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.loop import AgentLoop, _Turn
client = TestClient(app)
run, attempt = sys.argv[1:]
headers = {"X-Service-Token": "test-service", "X-Run-Attempt": attempt}
class Platform:
    async def get_run_state(self, rid):
        return client.get(f"/internal/runs/{rid}/state", headers=headers).json()
    async def claim_steers(self, rid):
        response = client.post(f"/internal/runs/{rid}/steer/claim", headers=headers)
        assert response.status_code == 200, response.text
        os.kill(os.getpid(), signal.SIGKILL)
loop = AgentLoop(platform=Platform(), broker=object())
loop._states[run] = fresh_state()
loop._steps[run] = 0
asyncio.run(loop._apply_steers(run, _Turn({})))
"""
    proc = subprocess.run(
        [sys.executable, "-c", code, run, headers["X-Run-Attempt"]], env=os.environ.copy(), capture_output=True, timeout=30
    )
    assert proc.returncode == -signal.SIGKILL, proc.stderr.decode()
    with SessionLocal() as db:
        assert db.get(QueuedMessage, qid).state == "steered"
    assert action(api, run, "release", headers["X-Run-Attempt"]).status_code == 200
    headers = {**SERVICE, "X-Run-Attempt": claim(api, run)["token"]}

    class Platform:
        async def get_run_state(self, rid):
            return api.get(f"/internal/runs/{rid}/state", headers=headers).json()

        async def claim_steers(self, rid):
            return api.post(f"/internal/runs/{rid}/steer/claim", headers=headers).json()

        async def put_checkpoint(self, rid, payload):
            response = api.put(f"/internal/runs/{rid}/checkpoint", headers=headers, json=payload)
            assert response.status_code == 200, response.text
            return response.json()

    loop = AgentLoop(platform=Platform(), broker=object())
    loop._states[run] = fresh_state()
    loop._steps[run] = 0
    turn = _Turn({})
    asyncio.run(loop._apply_steers(run, turn))
    asyncio.run(loop._apply_steers(run, turn))
    saved = api.get(f"/internal/runs/{run}/checkpoint", headers=headers).json()
    assert saved["state"]["messages"] == [{"role": "user", "content": "durable steer"}]
    assert saved["state"]["steer_ids"] == [qid]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.run_id == run, RunEvent.type == "agent.steered")) == 1
