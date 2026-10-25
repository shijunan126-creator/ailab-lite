"""Redelivery after a persisted quota pause cannot run work or enqueue a continuation."""

from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import OutboxMessage, Run
from tests.faults.test_transactional_jobs import api  # noqa: F401
from tests.test_agent_loop import ScriptedLLM
from tests.test_ledger_protocol import AUTH
from tests.test_quota_pause import Broker, process, seeded
from tests.test_transactional_jobs import action, claim


def test_quota_pause_redelivery_is_inert_and_old_ack_cannot_eat_manual_resume(api, monkeypatch):  # noqa: F811
    rid, token, _ = seeded(api)
    monkeypatch.setattr(settings, "daily_token_limit", 0)
    process(rid, token, ScriptedLLM([]), Broker(rid))
    paused = api.get(f"/v1/runs/{rid}", headers=AUTH).json()
    assert paused["status"] == "interrupted"
    # Simulate lost ACK: same owned job reaches the Agent again before transport acknowledgement.
    model = ScriptedLLM([])
    process(rid, token, model, Broker(rid))
    assert not model.seen_messages
    assert api.get(f"/v1/runs/{rid}", headers=AUTH).json() == paused
    monkeypatch.setattr(settings, "daily_token_limit", 100_000_000)
    assert api.post(f"/v1/runs/{rid}/resume", headers=AUTH).status_code == 200
    with SessionLocal() as db:
        job = db.get(Run, rid).runtime_meta["job_id"]
    assert action(api, rid, "ack", token).status_code == 200
    with SessionLocal() as db:
        assert db.get(OutboxMessage, job) is not None
    assert claim(api, job)["state"] == "claimed"
