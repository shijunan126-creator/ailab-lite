"""Additive internal HTTP contract, including machine-readable lease loss."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ailab_platform.broker import main
from ailab_platform.broker.service import BrokerOwnershipError, LeaseCanceledError
from ailab_platform.config import settings

from .test_broker_lease import _manager


@pytest.fixture
def broker(monkeypatch):
    manager, provider, _ = _manager()
    monkeypatch.setattr(main, "get_manager", lambda: manager)
    monkeypatch.setattr(settings, "sandbox_wait_seconds", 0)
    with TestClient(main.app) as client:
        yield client, manager, provider


def test_renew_status_and_typed_lost_error(broker):
    client, manager, provider = broker
    headers = {"X-Service-Token": settings.service_token}
    created = client.post("/internal/leases", headers=headers, json={"run_id": "r1"})
    assert created.status_code == 201
    lease_id = created.json()["lease_id"]
    assert client.get(f"/internal/leases/{lease_id}/status", headers=headers).json()["state"] == "active"
    assert client.post(f"/internal/leases/{lease_id}/renew", headers=headers).status_code == 200
    assert client.get("/internal/admission/r1", headers=headers).json() == {"status": "absent"}
    provider.release(manager.get(lease_id)["provider_ref"])
    lost = client.post(f"/internal/leases/{lease_id}/exec", headers=headers, json={"cmd": "echo hi"})
    assert lost.status_code == 410
    assert lost.json()["detail"]["code"] == "lease_lost"
    assert lost.json()["detail"]["recoverable"] is True
    assert client.get(f"/internal/leases/{lease_id}/status", headers=headers).json()["state"] == "failed"
    assert client.post(f"/internal/leases/{lease_id}/renew").status_code == 403


def test_expired_heartbeat_and_admission_deadline(broker):
    client, manager, _ = broker
    headers = {"X-Service-Token": settings.service_token}
    lease = manager.create("run-expiry")
    queued = client.post("/internal/leases", headers=headers, json={"run_id": "r2"})
    assert queued.status_code == 429
    assert queued.json()["detail"]["code"] == "sandbox_queue_deadline"
    lease["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    manager._save(lease)
    expired = client.post(f"/internal/leases/{lease['lease_id']}/renew", headers=headers)
    assert expired.status_code == 410
    assert expired.json()["detail"]["code"] == "lease_expired"


def test_startup_recovery_failure_is_fatal(monkeypatch):
    class Unavailable:
        def recover(self):
            raise BrokerOwnershipError("second broker")

        def close(self):
            pass

    monkeypatch.setattr(main, "get_manager", lambda: Unavailable())
    with pytest.raises(BrokerOwnershipError, match="second broker"), TestClient(main.app):
        pass


def test_canceled_lease_is_a_conflict(broker, monkeypatch):
    client, manager, _ = broker

    def canceled(**kwargs):
        raise LeaseCanceledError("任务已结束或已取消")

    monkeypatch.setattr(manager, "create", canceled)
    response = client.post("/internal/leases", headers={"X-Service-Token": settings.service_token}, json={"run_id": "canceled"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "run_canceled"
