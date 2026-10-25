"""Real-provider lifecycle contract. Opt in only against disposable sandbox backends."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pytest

from ailab_platform.config import settings
from ailab_platform.sandbox.base import SandboxSpec
from ailab_platform.sandbox.docker import DockerSandbox
from ailab_platform.sandbox.mock import MockSandbox
from ailab_platform.sandbox.opensandbox import OpenSandboxProvider


@pytest.fixture(params=["mock", "docker", "opensandbox"])
def provider(request, tmp_path, monkeypatch):
    # OpenSandbox's Docker backend shares the daemon with direct Docker tests.
    # Distinct owners prevent either reconciler adopting/deleting the other's resources.
    monkeypatch.setattr(settings, "broker_owner", f"contract-{request.param}-{uuid.uuid4().hex}")
    if request.param == "docker":
        if os.environ.get("AILAB_TEST_DOCKER") != "1":
            pytest.skip("set AILAB_TEST_DOCKER=1 with a disposable Docker runtime and sandbox image")
        value = DockerSandbox()
    elif request.param == "opensandbox":
        url = os.environ.get("AILAB_TEST_OSB_URL")
        if not url:
            pytest.skip("set AILAB_TEST_OSB_URL for a disposable OpenSandbox server")
        pytest.importorskip("opensandbox")
        parsed = urlparse(url)
        monkeypatch.setattr(settings, "osb_domain", parsed.netloc)
        monkeypatch.setattr(settings, "osb_protocol", parsed.scheme)
        monkeypatch.setattr(settings, "osb_request_timeout_seconds", 30)
        value = OpenSandboxProvider()
    else:
        value = MockSandbox()
        value.root = tmp_path / "sandboxes"
        value.root.mkdir()
    yield value
    if request.param == "opensandbox":
        value._manager.close()


def test_ownership_inspect_renew_reconnect_and_confirmed_delete(provider):
    now = datetime.now(UTC)
    lease_id, owner = f"contract-{uuid.uuid4().hex[:12]}", f"contract-{uuid.uuid4().hex}"
    labels = {
        "ailab.owner": owner,
        "ailab.lease_id": lease_id,
        "ailab.run_id": "run-contract",
        "ailab.user_id": "user-contract",
        "ailab.created_at": now.isoformat(),
        "ailab.state": "creating",
    }
    spec = SandboxSpec(image=settings.sandbox_image, ttl=60)
    ref = provider.acquire(lease_id, spec.image, ttl_seconds=spec.ttl, spec=spec, labels=labels)
    try:
        inspected = provider.inspect(ref)
        assert inspected and inspected.running
        assert labels.items() <= inspected.labels.items()
        assert [r.provider_ref for r in provider.list_owned(owner)] == [inspected.provider_ref]
        until = now + timedelta(seconds=90)
        provider.renew(ref, until)
        provider.update_metadata(ref, {"ailab.state": "active", "ailab.expires_at": until.isoformat()})
        assert provider.inspect(ref).labels["ailab.state"] == "active"
        if provider.name == "opensandbox":
            # Lifecycle operations must work without the in-process exec cache.
            for sandbox in provider._cache.values():
                sandbox.close()
            provider._cache.clear()
        assert provider.exec(ref, "printf contract", 10).stdout == "contract"
        provider.put_file(ref, "/workspace/a.txt", b"bytes")
        assert provider.get_file(ref, "/workspace/a.txt") == b"bytes"
    finally:
        provider.release(ref)
    assert provider.inspect(ref) is None
    provider.release(ref)  # a confirmed absence is idempotent


def test_docker_expiry_removes_resource_without_broker(monkeypatch):
    import time
    from datetime import UTC, datetime, timedelta

    from ailab_platform.sandbox import docker as module

    if os.getenv("AILAB_TEST_DOCKER") != "1":
        pytest.skip("AILAB_TEST_DOCKER=1 required")
    monkeypatch.setattr(module, "SANDBOX_EXPIRY_GRACE_SECONDS", 0)
    provider = module.DockerSandbox()
    spec = SandboxSpec(image=settings.sandbox_image, ttl=3, max_lifetime=15)
    started = time.monotonic()
    ref = provider.acquire("expiry-" + uuid.uuid4().hex, spec.image, ttl_seconds=3, spec=spec, labels={"ailab.owner": "expiry-contract"})
    try:
        assert provider.exec(ref, "test ! -w /run/ailab-lease.json", 5).exit_code == 0
        provider.renew(ref, datetime.now(UTC) + timedelta(seconds=6))
        while time.monotonic() - started < 3.3:
            time.sleep(0.1)
        assert provider.inspect(ref) is not None  # renewal extended the initial deadline
        deadline = time.monotonic() + 8
        while provider.inspect(ref) is not None and time.monotonic() < deadline:
            time.sleep(0.2)
        assert provider.inspect(ref) is None  # PID 1 + --rm, no Broker/cleanup process involved
    finally:
        provider.release(ref)


def test_cancellation_in_queue_and_after_real_creation(provider, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from ailab_platform.adapters.lease_store_memory import MemoryLeaseStore
    from ailab_platform.broker.service import LeaseCanceledError, LeaseManager
    from ailab_platform.domain.admission import LeasePrincipal

    monkeypatch.setattr(settings, "sandbox_max_per_user", 1)
    monkeypatch.setattr(settings, "broker_owner", "cancel-" + uuid.uuid4().hex)
    manager = LeaseManager(provider=provider, platform=object(), storage=object(), store=MemoryLeaseStore(), start_background=False)
    canceled, queued, created, resume = (threading.Event() for _ in range(4))
    spec = SandboxSpec(image=settings.sandbox_image, ttl=60, max_lifetime=120)

    def active():
        if canceled.is_set():
            raise LeaseCanceledError("canceled")

    hold = manager.acquire(spec, LeasePrincipal("qa", "hold"), wait_seconds=0)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                manager.acquire,
                spec,
                LeasePrincipal("qa", "queued"),
                wait_seconds=10,
                ensure_active=active,
                on_queued=lambda _: queued.set(),
            )
            assert queued.wait(5)
            canceled.set()
            with pytest.raises(LeaseCanceledError):
                future.result(timeout=5)
        assert len(list(provider.list_owned(settings.broker_owner))) == 1
        manager.release(hold)
        canceled.clear()
        acquire = provider.acquire
        refs = []

        def slow_create(*args, **kwargs):
            ref = acquire(*args, **kwargs)
            refs.append(ref)
            created.set()
            assert resume.wait(10)
            return ref

        monkeypatch.setattr(provider, "acquire", slow_create)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(manager.acquire, spec, LeasePrincipal("qa", "creating"), ensure_active=active)
            try:
                assert created.wait(45)
                canceled.set()
            finally:
                resume.set()
            with pytest.raises(LeaseCanceledError):
                future.result(timeout=20)
        assert refs and provider.inspect(refs[0]) is None
        assert not list(provider.list_owned(settings.broker_owner))
    finally:
        resume.set()
        for lease in manager.active_leases():
            manager.release(lease["lease_id"])
        manager.close()


def test_stale_listing_is_confirmed_before_real_resource_deletion(provider, monkeypatch):
    from dataclasses import replace

    from ailab_platform.adapters.lease_store_memory import MemoryLeaseStore
    from ailab_platform.broker.service import LeaseManager
    from ailab_platform.domain.admission import LeasePrincipal

    monkeypatch.setattr(settings, "broker_owner", "stale-" + uuid.uuid4().hex)
    manager = LeaseManager(provider=provider, platform=object(), storage=object(), store=MemoryLeaseStore(), start_background=False)
    handle = manager.acquire(SandboxSpec(image=settings.sandbox_image, ttl=60, max_lifetime=120), LeasePrincipal("qa", "stale"))
    listing = provider.list_owned
    try:
        monkeypatch.setattr(provider, "list_owned", lambda owner: [replace(r, running=False) for r in listing(owner)])
        manager.cleanup_once()
        assert manager.status(handle)["state"] == "active"
        assert provider.exec(handle.provider_ref, "printf still-running", 10).stdout == "still-running"
    finally:
        manager.release(handle)
        manager.close()


def test_exec_deadline_and_cancellation_stop_real_work(provider):
    import time
    from concurrent.futures import ThreadPoolExecutor

    from ailab_platform.sandbox.base import SandboxError

    spec = SandboxSpec(image=settings.sandbox_image, ttl=120)
    ref = provider.acquire("exec-" + uuid.uuid4().hex[:12], spec.image, spec=spec)
    try:
        started = time.monotonic()
        result = provider.exec(ref, "sleep 60", 1)
        assert result.exit_code != 0
        assert time.monotonic() - started < 15
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(provider.exec, ref, "echo ready > /workspace/started; sleep 60; echo completed", 70)
            try:
                deadline = time.monotonic() + 15
                while provider.exec(ref, "test -f /workspace/started", 5).exit_code != 0:
                    assert time.monotonic() < deadline, "long command never started"
                    time.sleep(0.1)
                if not provider.cancel_exec(ref):
                    provider.release(ref)
                try:
                    result = future.result(timeout=15)
                except SandboxError:
                    assert provider.inspect(ref) is None
                else:
                    assert result.exit_code != 0 and "completed" not in result.stdout
            finally:
                # A failed interrupt must not leave a minute-long command or executor behind.
                provider.release(ref)
    finally:
        provider.release(ref)
