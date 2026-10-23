"""The API clock counts RUNNING intervals once, even across sessions and redelivery."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from ailab_platform import run_state
from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import Run
from tests.test_run_state import AUTH, _new_run


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "canceled"])
def test_work_time_excludes_queue_wait_pause_and_survives_reload(monkeypatch, terminal):
    start = datetime(2026, 1, 1, tzinfo=UTC)

    class Clock:
        value = start

        @classmethod
        def now(cls, tz=None):
            return cls.value

    monkeypatch.setattr(run_state, "datetime", Clock)
    with TestClient(app) as client:
        rid = _new_run(client)

        def move(seconds, status, expected):
            Clock.value = start + timedelta(seconds=seconds)
            with SessionLocal() as db:
                run_state.transition_run(db, rid, status)
            payload = client.get(f"/v1/runs/{rid}", headers=AUTH).json()
            assert payload["work_ms"] == expected

        move(10, "running", 0)
        move(20, "running", 0)  # duplicate start cannot reset the interval
        move(25, "waiting_user", 15000)
        move(90, "queued", 15000)
        move(100, "running", 15000)
        move(107, "interrupted", 22000)
        move(180, "queued", 22000)
        move(200, "running", 22000)
        move(203.75, terminal, 25750)
        move(300, "succeeded", 25750)  # late completion and duplicate deliveries are final
        with SessionLocal() as db:
            run = db.get(Run, rid)
            assert run.work_started_at is None
        assert client.get(f"/v1/runs/{rid}", headers=AUTH).json()["work_ms"] == 25750


def test_cancel_before_start_is_zero_and_legacy_history_is_not_invented():
    with TestClient(app) as client:
        rid = _new_run(client)
        assert client.post(f"/v1/runs/{rid}/cancel", headers=AUTH).json()["work_ms"] == 0
        legacy = _new_run(client)
        with SessionLocal() as db:
            db.execute(update(Run).where(Run.id == legacy).values(work_ms=None))
            db.commit()
            run_state.transition_run(db, legacy, "running")
            run_state.transition_run(db, legacy, "succeeded")
        assert client.get(f"/v1/runs/{legacy}", headers=AUTH).json()["work_ms"] is None


def test_process_history_is_bounded_and_keyset_pages_do_not_overlap():
    from ailab_platform.events import append_event

    with TestClient(app) as client:
        rid = _new_run(client)
        with SessionLocal() as db:
            for i in range(205):
                append_event(db, rid, "agent.delta", {"step": 0, "text": str(i)}, commit=False)
            db.commit()
        first = client.get(f"/v1/runs/{rid}/events/history", headers=AUTH).json()
        assert len(first) == 200
        second = client.get(f"/v1/runs/{rid}/events/history?after={first[-1]['seq']}", headers=AUTH).json()
        assert len(second) == 6 and first[-1]["seq"] < second[0]["seq"]
        assert client.get(f"/v1/runs/{rid}/events/history?limit=501", headers=AUTH).status_code == 422
