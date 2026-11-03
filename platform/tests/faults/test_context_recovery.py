"""Killed context writer, ownership loss and stale compaction activation."""

import asyncio
import copy
import json
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.loop import AgentLoop
from ailab_platform.catalog import Catalog, ModelSpec
from ailab_platform.clients import ClientError
from ailab_platform.contracts.context_summary import ContextSummary, canonical_summary
from ailab_platform.db import SessionLocal
from ailab_platform.llm import LLMResponse
from ailab_platform.model_transport import ModelTransport
from ailab_platform.models import OutboxMessage
from tests.faults.test_long_run_recovery import platform_client
from tests.faults.test_transactional_jobs import api  # noqa: F401
from tests.prefix_harness import scripted_server
from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM
from tests.test_context_isolation import conversation_candidate
from tests.test_ledger_protocol import SERVICE
from tests.test_runtime_protocol import save
from tests.test_transactional_jobs import action, claim, start


def test_sigkill_restores_exact_wire_context_and_epoch_in_new_worker(api):  # noqa: F811
    rid = start(api)
    program = """
import asyncio, os, signal, sys
from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.runtime_context import attempt_id_var
from tests.faults.test_long_run_recovery import platform_client
async def run():
    rid = sys.argv[1]
    platform = await platform_client()
    attempt = await platform.job_action(rid, "claim", consumer="context-crash")
    attempt_id_var.set(attempt["token"])
    await platform.transition_run(rid, "running")
    state = fresh_state()
    state.update(context_epoch=12, memory_observed={}, tool_catalog_v1=[], messages=[
        {"role":"system","content":"frozen platform bytes 中文"},
        {"role":"system","content":"frozen boundary bytes"},
        {"role":"user","content":"continue exactly"},
    ])
    await platform.put_checkpoint(rid, {"expected_step":0,"state":state})
    os.kill(os.getpid(), signal.SIGKILL)
asyncio.run(run())
"""
    result = subprocess.run([sys.executable, "-c", program, rid], env=os.environ.copy(), capture_output=True, timeout=30)
    assert result.returncode == -signal.SIGKILL, result.stderr.decode()
    before = api.get(f"/internal/runs/{rid}/checkpoint", headers=SERVICE).json()
    assert before["state"]["context_epoch"] == 12
    with SessionLocal() as db:
        job = db.get(OutboxMessage, rid)
        job.claimed_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    replacement = claim(api, rid)
    assert replacement["attempt"] == 2

    async def recover(url):
        platform = await platform_client()
        try:
            await AgentLoop(platform, FakeBroker(), ModelTransport(url)).process(rid, attempt=2, attempt_id=replacement["token"])
        finally:
            await platform.aclose()

    with scripted_server([{"content": "recovered"}]) as (url, requests):
        asyncio.run(recover(url))
    assert len(requests) == 1
    assert json.dumps(requests[0]["messages"], ensure_ascii=False) == json.dumps(before["state"]["messages"], ensure_ascii=False)
    after = api.get(f"/internal/runs/{rid}/checkpoint", headers=SERVICE).json()
    assert after["state"]["context_epoch"] == 12
    assert after["state"]["messages"][:-1] == before["state"]["messages"]


def test_lost_attempt_cannot_append_settle_tools_or_activate_epoch_and_summary(api):  # noqa: F811
    _, rid, headers, summary = conversation_candidate(api)
    token = headers["X-Run-Attempt"]
    initial = {**fresh_state(), "context_epoch": 5, "messages": [{"role": "user", "content": "original"}]}
    assert save(api, rid, headers, state=initial).status_code == 200
    assert action(api, rid, "release", token).status_code == 200
    replacement = claim(api, rid)
    live = {**SERVICE, "X-Run-Attempt": replacement["token"]}
    candidate = {**copy.deepcopy(initial), "context_epoch": 6}
    candidate["messages"].append({"role": "assistant", "content": "stale compacted summary"})
    candidate["tool_log"] = {"0:0": {"name": "exec", "result": {"ok": True}}}
    assert save(api, rid, headers, step=1, state=candidate).status_code == 409
    response = api.post(
        f"/internal/runs/{rid}/events", headers=headers, json={"type": "agent.context_boundary", "payload": {"reason": "compacted"}}
    )
    assert response.status_code == 409 and response.json()["detail"] == "stale_attempt"
    assert (
        api.put(
            f"/internal/runs/{rid}/model-summary",
            headers=headers,
            json={**summary, "summary": canonical_summary(ContextSummary(objective="stale"))},
        ).status_code
        == 409
    )
    assert api.get(f"/internal/runs/{rid}/checkpoint", headers=SERVICE).json()["state"] == initial
    assert save(api, rid, live, step=1, state=initial).status_code == 200


@pytest.mark.parametrize("conflict", ["stale_step", "stale_attempt"])
def test_compaction_discards_rejected_epoch_messages_and_memory(monkeypatch, conflict):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=10000, max_output_tokens=1000)
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    monkeypatch.setattr("ailab_platform.agent.loop.estimator.estimate", lambda model, value: 9000 if "OLDER" in json.dumps(value) else 100)

    class Platform(FakePlatform):
        async def get_memory_context(self, run_id):
            return {"enabled": True, "entries": [{"id": "new", "content": "fresh memory"}]}

        async def put_checkpoint(self, *args):
            raise ClientError("PUT", "/checkpoint", 409, conflict)

    loop = AgentLoop(Platform(), llm=ScriptedLLM([LLMResponse(content=canonical_summary(ContextSummary()))]))
    state = {
        **fresh_state(),
        "context_epoch": 4,
        "user_message_index": 2,
        "memory_observed": {"revision": 1},
        "messages": [
            {"role": "system", "content": "rules"},
            {"role": "system", "content": "boundary"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "OLDER"},
        ],
    }
    loop._states["r"], loop._steps["r"] = state, 1
    turn = SimpleNamespace(messages=state["messages"], run={"_attempt_id": "a"}, artifacts=[], delivered={})
    before = copy.deepcopy(state)
    with pytest.raises(ClientError):
        asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", []))
    assert state == before
    assert not any(kind == "agent.context_boundary" for kind, _ in loop.platform.events)


def test_rejected_boundary_event_cannot_advance_local_epoch():
    class Platform(FakePlatform):
        async def put_checkpoint(self, *args):
            raise ClientError("POST", "/events", 409, "stale_attempt")

    loop = AgentLoop(Platform())
    loop._states["r"] = {**fresh_state(), "context_epoch": 4}
    before = copy.deepcopy(loop._states["r"])
    with pytest.raises(ClientError):
        asyncio.run(loop._event("r", "agent.context_boundary", {"reason": "compacted"}))
    assert loop._states["r"] == before
