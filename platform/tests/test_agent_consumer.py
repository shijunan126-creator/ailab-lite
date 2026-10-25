"""Run-message handling on the durable path: claim, ACK only after the work, hand-back, busy and duplicate jobs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ailab_platform.agent import main as agent_main
from ailab_platform.config import settings
from ailab_platform.contracts.jobs import JobEnvelope
from ailab_platform.queue import InMemoryQueue


def _envelope(run_id="run_1", mode="work") -> dict:
    now = datetime.now(UTC)
    return JobEnvelope(job_id=run_id, kind="run", run_id=run_id, mode=mode, deadline=now + timedelta(hours=1), enqueued_at=now).model_dump(
        mode="json"
    )


class FakePlatform:
    """The API's job and run endpoints as the Agent sees them: one claim, the run's state, and every job action."""

    claim: dict = {}
    run_state: dict = {}
    statuses: list = []
    actions: list = []

    async def aclose(self):
        pass

    async def job_action(self, job_id, action, **body):
        FakePlatform.actions.append((job_id, action, body))
        return dict(FakePlatform.claim) if action == "claim" else {"ok": True}

    async def get_run_state(self, run_id):
        return dict(FakePlatform.run_state)

    async def set_run_status(self, run_id, status, **kw):
        FakePlatform.statuses.append((run_id, status, kw))
        return {}


class FakeLoop:
    def __init__(self, behaviour="ok") -> None:
        self.behaviour = behaviour
        self.calls: list[tuple] = []

    async def process(self, run_id, attempt=1, attempt_id=None):
        self.calls.append((run_id, attempt, attempt_id))
        if self.behaviour == "hang":
            await asyncio.sleep(60)
        elif self.behaviour == "boom":
            raise RuntimeError("boom")
        elif self.behaviour == "cancel":
            raise asyncio.CancelledError()
        elif self.behaviour == "waiting":
            FakePlatform.run_state = {"status": "waiting_user", "job_id": "run_1"}


@pytest.fixture()
def env(monkeypatch):
    FakePlatform.statuses, FakePlatform.actions = [], []
    FakePlatform.claim = {"state": "claimed", "token": "tok_1", "attempt": 1, "attempt_id": "tok_1"}
    FakePlatform.run_state = {"status": "succeeded", "job_id": "run_1"}
    monkeypatch.setattr(agent_main, "AsyncPlatformClient", FakePlatform)
    monkeypatch.setattr(settings, "run_timeout_seconds", 1)
    q = InMemoryQueue()
    q.enqueue(settings.run_queue, _envelope())
    return q, q.dequeue(settings.run_queue, "c", timeout=1)


def _handle(loop, q, msg):
    asyncio.run(agent_main.handle_message(loop, q, msg))


def _actions(name):
    return [(job, body) for job, action, body in FakePlatform.actions if action == name]


def test_success_acks_the_job_with_its_claim_and_the_queue_message(env) -> None:
    q, msg = env
    loop = FakeLoop()
    _handle(loop, q, msg)
    assert loop.calls == [("run_1", 1, "tok_1")]  # the attempt the claim returned, not one the consumer made up
    assert _actions("ack") == [("run_1", {"token": "tok_1"})]
    assert q.outstanding(settings.run_queue) == []


def test_shutdown_releases_the_claim_and_hands_the_message_back(env) -> None:
    q, msg = env
    with pytest.raises(asyncio.CancelledError):
        _handle(FakeLoop("cancel"), q, msg)
    assert _actions("release") == [("run_1", {"token": "tok_1"})]
    assert _actions("ack") == []
    assert q.outstanding(settings.run_queue) == []


def test_redelivered_job_reports_the_attempt_the_claim_counted(env) -> None:
    q, msg = env
    FakePlatform.claim = {"state": "claimed", "token": "tok_2", "attempt": 2, "attempt_id": "tok_2"}
    loop = FakeLoop()
    _handle(loop, q, msg)
    assert loop.calls == [("run_1", 2, "tok_2")]


def test_old_total_timeout_no_longer_kills_active_run(env) -> None:
    q, msg = env

    class Slow(FakeLoop):
        async def process(self, *args, **kwargs):
            await asyncio.sleep(1.1)  # exceeds the legacy 1-second setting

    _handle(Slow(), q, msg)
    assert FakePlatform.statuses == []
    assert _actions("ack") == [("run_1", {"token": "tok_1"})]


def test_unexpected_crash_fails_the_run_and_acks(env) -> None:
    q, msg = env
    _handle(FakeLoop("boom"), q, msg)
    assert FakePlatform.statuses[-1][:2] == ("run_1", "failed")
    assert _actions("ack") == [("run_1", {"token": "tok_1"})]
    assert q.outstanding(settings.run_queue) == []


def test_a_run_waiting_for_an_answer_is_acked_without_failing(env) -> None:
    # waiting_user and interrupted are resting states: the job is done, the answer or resume creates the next one
    q, msg = env
    _handle(FakeLoop("waiting"), q, msg)
    assert FakePlatform.statuses == []
    assert _actions("ack") == [("run_1", {"token": "tok_1"})]


def test_a_run_still_in_progress_keeps_its_durable_job(env) -> None:
    q, msg = env
    FakePlatform.run_state = {"status": "running", "job_id": "run_1"}
    with pytest.raises(RuntimeError, match="retain durable job"):
        _handle(FakeLoop(), q, msg)
    assert _actions("ack") == []


def test_a_claim_held_elsewhere_is_dropped_without_running(env) -> None:
    q, msg = env
    FakePlatform.claim = {"state": "busy"}
    loop = FakeLoop()
    _handle(loop, q, msg)
    assert loop.calls == []
    assert _actions("ack") == []
    assert q.outstanding(settings.run_queue) == []


def test_a_message_without_a_durable_job_is_dropped(env) -> None:
    q, first = env
    q.ack(settings.run_queue, first)
    q.enqueue(settings.run_queue, {"run_id": "run_9"})
    loop = FakeLoop()
    _handle(loop, q, q.dequeue(settings.run_queue, "c", timeout=1))
    assert loop.calls == []
    assert q.outstanding(settings.run_queue) == []


def test_sigterm_cancels_the_consumer_at_once_and_keeps_uvicorns_handler(monkeypatch) -> None:
    import signal as _signal

    from ailab_platform.agent import main as agent_main

    calls = []

    async def scenario():
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(asyncio.sleep(3600))
        monkeypatch.setattr(agent_main, "_task", task)
        previous = _signal.getsignal(_signal.SIGTERM)
        _signal.signal(_signal.SIGTERM, lambda s, f: calls.append("uvicorn"))
        try:
            agent_main._chain_sigterm(loop)
            _signal.getsignal(_signal.SIGTERM)(_signal.SIGTERM, None)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert task.cancelled()
        finally:
            _signal.signal(_signal.SIGTERM, previous)

    asyncio.run(scenario())
    assert calls == ["uvicorn"]


def test_live_job_is_renewed_on_short_clock_and_failure_stops_unfenced_work(env, monkeypatch):
    q, msg = env
    monkeypatch.setattr(settings, "job_lease_seconds", 0.03)
    canceled = []

    class RenewalFailure(FakePlatform):
        async def job_action(self, job_id, action, **body):
            await super().job_action(job_id, action, **body)
            if action == "renew" and len(_actions("renew")) == 2:
                raise ConnectionError("API unavailable")
            return dict(self.claim) if action == "claim" else {"ok": True}

    class Working(FakeLoop):
        async def process(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                canceled.append(True)

    monkeypatch.setattr(agent_main, "AsyncPlatformClient", RenewalFailure)
    with pytest.raises(ConnectionError):
        _handle(Working(), q, msg)
    assert len(_actions("renew")) == 2 and canceled
    assert not _actions("ack") and FakePlatform.statuses == []
