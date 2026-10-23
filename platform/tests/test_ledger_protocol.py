"""Acceptance of call-level hard quota and immutable settlement on both SQL backends."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import ceil
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ailab_platform.api.main import app
from ailab_platform.config import Settings, settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import ModelUsage, Run, UsageReservation
from ailab_platform.ops.reconcile_usage import reconcile
from ailab_platform.quota import expire_reservations, quota_windows
from tests.usage_helpers import owned_headers

AUTH = {"Authorization": "Bearer test-token"}
SERVICE = {"X-Service-Token": "test-service"}


@pytest.fixture
def api():
    with TestClient(app) as client:
        yield client
    with SessionLocal() as db:
        # Keep this shared test database free of open intents for subsequent quota tests.
        for row in db.scalars(select(UsageReservation).where(UsageReservation.status == "open")):
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        expire_reservations(db, datetime.now(UTC))


def reservation(api, **overrides):
    run = api.post("/v1/runs", headers=AUTH, json={"prompt": "ledger", "model": "test-fast"})
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]
    body = {
        "call_id": uuid4().hex,
        "run_id": run_id,
        "attempt_id": owned_headers(api, run_id)["X-Run-Attempt"],
        "alias": "test-fast",
        "mode": "work",
        "max_output_tokens": 10,
        "estimated_input_tokens": 10,
        **overrides,
    }
    return body


def reserve(api, body):
    result = api.post("/internal/usage/reserve", headers=SERVICE, json=body)
    assert result.status_code == 200, result.text
    return result.json()


def settle(api, call_id, **overrides):
    # A late settlement authenticates the attempt that reserved the call, whether or not it still owns the job.
    with SessionLocal() as db:
        frozen = db.get(UsageReservation, call_id)
    body = {
        "call_id": call_id,
        "attempt_id": frozen.attempt_id if frozen else "unknown",
        "actual_backend": "backend-revision-1",
        "input_tokens": 10,
        "cached_tokens": 5,
        "output_tokens": 4,
        "reasoning_tokens": 2,
        "status": "settled",
        **overrides,
    }
    result = api.post("/internal/usage/settle", headers=SERVICE, json=body)
    assert result.status_code == 200, result.text
    return result.json()


def test_duplicate_reserve_and_concurrent_settle_are_idempotent(api):
    body = reservation(api)
    first = reserve(api, body)
    assert reserve(api, body) == first
    with ThreadPoolExecutor(2) as pool:
        rows = list(pool.map(lambda _: settle(api, body["call_id"]), range(2)))
    assert rows[0] == rows[1]
    assert rows[0]["weighted_amount"] == 9500  # reasoning is part of output, not added twice
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ModelUsage).where(ModelUsage.call_id == body["call_id"])) == 1
        ledger = db.scalar(select(ModelUsage).where(ModelUsage.call_id == body["call_id"]))
        assert ledger.attempt_id == body["attempt_id"] and ledger.mode == "work" and ledger.alias == "test-fast"
        assert ledger.actual_backend == "backend-revision-1" and ledger.dept_id is None
    conflicting = api.post("/internal/usage/reserve", headers=SERVICE, json={**body, "max_output_tokens": 11})
    assert conflicting.status_code == 409 and conflicting.json()["detail"]["code"] == "call_conflict"
    foreign = api.post("/internal/usage/reserve", headers=SERVICE, json={**body, "attempt_id": "different"})
    assert foreign.status_code == 409 and foreign.json()["detail"] == "stale_attempt"
    assert settle(api, body["call_id"], input_tokens=100)["weighted_amount"] == 9500


def test_cached_price_is_frozen_at_reservation_and_history_never_reprices(api, monkeypatch):
    before = api.get("/v1/usage", headers=AUTH).json()["daily_tokens"]["used"]
    body = reservation(api)
    reserve(api, body)
    monkeypatch.setattr(settings, "cached_token_weight", 0.8)
    row = settle(api, body["call_id"])
    assert row["weighted_amount"] == 9500
    assert api.get("/v1/usage", headers=AUTH).json()["daily_tokens"]["used"] - before == 9.5
    monkeypatch.setattr(settings, "cached_token_weight", 0.99)
    assert api.get("/v1/usage", headers=AUTH).json()["daily_tokens"]["used"] - before == 9.5


def test_expired_intent_charges_conservatively_and_late_settle_corrects_one_row(api):
    body = reservation(api)
    result = reserve(api, body)
    with SessionLocal() as db:
        expired = datetime.now(UTC) + timedelta(seconds=settings.usage_reservation_ttl_seconds + 1)
        assert expire_reservations(db, expired) == 1
        assert expire_reservations(db, expired) == 0
        row = db.scalar(select(ModelUsage).where(ModelUsage.call_id == body["call_id"]))
        ledger_id = row.id
        assert row.status == "unreconciled" and row.weighted_amount == result["reserved_amount"]
        assert db.get(UsageReservation, body["call_id"]).status == "unreconciled"
    settled = settle(api, body["call_id"])
    assert settled["status"] == "settled" and settled["weighted_amount"] == 9500
    with SessionLocal() as db:
        assert db.scalar(select(ModelUsage.id).where(ModelUsage.call_id == body["call_id"])) == ledger_id
        totals = db.get(Run, body["run_id"]).result["usage_totals"]
        assert totals["prompt_tokens"] == 10 and totals["completion_tokens"] == 4


def test_two_runs_share_one_atomic_hard_quota(api, monkeypatch):
    bodies = [reservation(api), reservation(api)]
    usage = api.get("/v1/usage", headers=AUTH).json()["daily_tokens"]
    monkeypatch.setattr(settings, "daily_token_limit", int(usage["used"] + usage["reserved"]) + 25)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda b: api.post("/internal/usage/reserve", headers=SERVICE, json=b), bodies))
    assert sorted(r.status_code for r in results) == [200, 429]
    error = next(r for r in results if r.status_code == 429).json()["detail"]
    assert error["code"] == "quota_exceeded" and error["window"] == "daily_tokens"
    assert datetime.fromisoformat(error["resets_at"]).tzinfo is not None
    admitted = next(b for b, r in zip(bodies, results, strict=True) if r.status_code == 200)
    settle(api, admitted["call_id"], input_tokens=0, cached_tokens=0, output_tokens=0, reasoning_tokens=0, status="released")
    rejected = next(b for b, r in zip(bodies, results, strict=True) if r.status_code == 429)
    reserve(api, rejected)


def test_expiry_never_opens_a_gap_and_open_intents_survive_period_rollover(api, monkeypatch):
    body = reservation(api)
    reserve(api, body)
    with SessionLocal() as db:
        row = db.get(UsageReservation, body["call_id"])
        row.created_at -= timedelta(days=40)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    usage = api.get("/v1/usage", headers=AUTH).json()
    assert usage["daily_tokens"]["reserved"] >= 20 and usage["monthly_tokens"]["reserved"] >= 20
    # An expired reservation is not dropped from admission before being charged.
    monkeypatch.setattr(settings, "daily_token_limit", 0)
    duplicate = reserve(api, body)
    assert duplicate["status"] == "open"


def test_monthly_hard_quota_and_alerts_include_reservations(api, monkeypatch):
    body = reservation(api)
    usage = api.get("/v1/usage", headers=AUTH).json()
    monthly = usage["monthly_tokens"]
    limit = ceil(monthly["used"] + monthly["reserved"]) + 20
    monkeypatch.setattr(settings, "monthly_token_limit", limit)
    reserve(api, body)
    after = api.get("/v1/usage", headers=AUTH).json()
    assert after["monthly_tokens"]["percent"] >= 99
    monkeypatch.setattr(settings, "monthly_token_limit", int(after["monthly_tokens"]["used"] + after["monthly_tokens"]["reserved"]))
    after = api.get("/v1/usage", headers=AUTH).json()
    assert {"code": "exceeded", "window": "monthly_tokens", "resets_at": after["monthly_tokens"]["resets_at"]} in after["alerts"]
    other = {**body, "call_id": uuid4().hex}
    response = api.post("/internal/usage/reserve", headers=SERVICE, json=other)
    assert response.status_code == 429 and response.json()["detail"]["window"] == "monthly_tokens"


def test_near_limit_banner_contract(api, monkeypatch):
    body = reservation(api, max_output_tokens=100)
    reserve(api, body)
    usage = api.get("/v1/usage", headers=AUTH).json()["daily_tokens"]
    monkeypatch.setattr(settings, "daily_token_limit", int((usage["used"] + usage["reserved"]) / 0.85))
    data = api.get("/v1/usage", headers=AUTH).json()
    assert set(data["daily_tokens"]) == {"used", "reserved", "limit", "percent", "resets_at"}
    assert any(a["code"] == "near_limit" and a["window"] == "daily_tokens" for a in data["alerts"])


def test_quota_periods_use_iana_timezone_and_dst_calendar_boundaries(monkeypatch):
    monkeypatch.setattr(settings, "quota_timezone", "America/New_York")
    windows = quota_windows(datetime(2026, 3, 8, 12, tzinfo=UTC))
    start, reset, _ = windows["daily_tokens"]
    assert start == datetime(2026, 3, 8, 5, tzinfo=UTC)
    assert reset == datetime(2026, 3, 9, 4, tzinfo=UTC)
    assert windows["monthly_tokens"][1] == datetime(2026, 4, 1, 4, tzinfo=UTC)
    with pytest.raises(ValueError, match="IANA timezone"):
        Settings(quota_timezone="no-such-zone")


def test_reconciliation_is_bounded_and_finds_recorded_total_drift(api):
    body = reservation(api)
    reserve(api, body)
    settle(api, body["call_id"], status="estimated")
    with SessionLocal() as db:
        row = db.scalar(select(ModelUsage).where(ModelUsage.call_id == body["call_id"]))
        run = db.get(Run, body["run_id"])
        run.result = {"usage_totals": {"weighted_amount": -1}}
        db.commit()
        report = reconcile(db, batch_size=1, after_usage_id=row.id - 1)
        assert report["usage"][0]["call_id"] == body["call_id"] and len(report["usage"]) == 1
        # Keyset scanning finds the corrupted total, even when no suspect usage row is in that batch.
        report = reconcile(db, batch_size=1000)
        assert any(r["run_id"] == run.id for r in report["run_mismatches"])
    with pytest.raises(ValueError):
        reconcile(None, 1001)


def test_protocol_rejects_unbound_and_unreserved_usage(api):
    body = reservation(api)
    assert api.post("/internal/usage/reserve", headers=SERVICE, json={**body, "user_id": "foreign"}).status_code == 422
    assert api.post("/internal/usage/reserve", json=body).status_code == 403
    assert (
        api.post(
            "/internal/usage/settle", headers=SERVICE, json={"call_id": "missing", "attempt_id": "x", "status": "released"}
        ).status_code
        == 404
    )
    api.post(f"/v1/runs/{body['run_id']}/cancel", headers=AUTH)
    result = api.post("/internal/usage/reserve", headers=SERVICE, json=body)
    assert result.status_code == 409 and result.json()["detail"]["code"] == "run_not_active"
    assert api.post(f"/internal/runs/{body['run_id']}/usage", headers=SERVICE, json={}).status_code == 410


def test_fractional_weights_round_up_once_to_integer_milli():
    from ailab_platform.domain.ledger import weighted_amount

    assert weighted_amount(1, 1, 0, Decimal("0.3"), Decimal("0.1")) == 30
    assert weighted_amount(1, 1, 0, Decimal("0.123456789"), Decimal("0.1")) == 13


def test_workspace_cleanup_cannot_overwrite_a_concurrent_late_settlement(api, monkeypatch):
    from ailab_platform.api import routes_internal
    from ailab_platform.models import Conversation

    old = reservation(api)
    reserve(api, old)
    newer = reservation(api)
    with SessionLocal() as db:
        earlier = db.get(Run, old["run_id"])
        current = db.get(Run, newer["run_id"])
        conversation = Conversation(user_id=earlier.user_id)
        db.add(conversation)
        db.flush()
        earlier.conversation_id = current.conversation_id = conversation.id
        earlier.result = {"workspace_saved": True}
        db.commit()

    class Storage:
        def delete(self, key):
            settle(api, old["call_id"])

    monkeypatch.setattr(routes_internal, "get_storage", Storage)
    with SessionLocal() as db:
        routes_internal._drop_older_workspaces(db, db.get(Run, newer["run_id"]))
        db.expire_all()
        result = db.get(Run, old["run_id"]).result
        assert result["workspace_saved"] is False
        assert result["usage_totals"]["weighted_amount"] == 9500


def test_usage_read_cannot_double_count_a_call_moving_to_the_ledger(api):
    from sqlalchemy import event

    from ailab_platform.db import engine

    before = api.get("/v1/usage", headers=AUTH).json()["daily_tokens"]["used"]
    body = reservation(api)
    reserve(api, body)
    moved = False

    def settle_between_separate_reads(conn, cursor, statement, parameters, context, executemany):
        nonlocal moved
        # A combined statement has one MVCC snapshot. A separate reserved SUM would
        # let the subsequent ledger SUM see this settlement as well as the old intent.
        if not moved and "sum(" in statement.lower() and "usage_reservations" in statement and "model_usage" not in statement:
            moved = True
            settle(api, body["call_id"], cached_tokens=0, output_tokens=10, reasoning_tokens=0)

    event.listen(engine, "after_cursor_execute", settle_between_separate_reads)
    try:
        window = api.get("/v1/usage", headers=AUTH).json()["daily_tokens"]
        assert window["used"] + window["reserved"] - before == 20
    finally:
        event.remove(engine, "after_cursor_execute", settle_between_separate_reads)
