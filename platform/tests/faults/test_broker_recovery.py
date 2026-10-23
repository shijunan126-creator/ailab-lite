"""Faults at the lease/resource boundary, with memory and real disposable Redis."""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pytest
import redis

from ailab_platform.adapters.lease_store_memory import MemoryLeaseStore
from ailab_platform.adapters.lease_store_redis import RedisLeaseStore
from ailab_platform.broker.service import (
    AdmissionRejected,
    AdmissionTimeout,
    BrokerOwnershipError,
    LeaseConflictError,
    LeaseExpiredError,
    LeaseLostError,
    LeaseManager,
)
from ailab_platform.config import settings
from ailab_platform.domain.admission import LeasePrincipal
from ailab_platform.sandbox.base import SandboxError, SandboxSpec
from ailab_platform.sandbox.mock import MockSandbox


@pytest.fixture(params=["memory", "redis"])
def env(request, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "broker_owner", f"test-{uuid.uuid4().hex}")
    monkeypatch.setattr(settings, "max_sandboxes", 3)
    monkeypatch.setattr(settings, "sandbox_total_memory_bytes", 6 * 1024**3)
    monkeypatch.setattr(settings, "sandbox_render_memory_bytes", 1024**3)
    monkeypatch.setattr(settings, "sandbox_render_reserved_slots", 1)
    monkeypatch.setattr(settings, "sandbox_max_per_user", 1)
    client = None
    if request.param == "redis":
        url = os.environ.get("AILAB_TEST_REDIS_URL")
        if not url:
            pytest.skip("set AILAB_TEST_REDIS_URL to throwaway Redis on port 16440")
        assert urlparse(url).port == 16440, "Broker faults may only flush the disposable Redis on 16440"
        client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        store = RedisLeaseStore(client, settings.broker_owner)
    else:
        store = MemoryLeaseStore()

    class Provider(MockSandbox):
        fail_delete = False
        deletes = 0

        def release(self, ref):
            self.deletes += 1
            if self.fail_delete:
                raise SandboxError("provider down")
            super().release(ref)

    provider = Provider()
    provider.root = tmp_path / "sandboxes"
    provider.root.mkdir()
    managers = []

    def make():
        manager = LeaseManager(
            provider=provider, platform=object(), storage=object(), redis_client=client, store=store, start_background=False
        )
        managers.append(manager)
        return manager

    def flush():
        if client:
            client.flushdb()
        else:
            store._records.clear()
            store._owner = None

    yield make, provider, store, flush
    for manager in managers:
        manager.close()
    if client:
        client.delete(store.key, store.live_key, store.owner_key)
        client.close()


def spec(**kwargs):
    return SandboxSpec(image="test-office", ttl=60, max_lifetime=120, **kwargs)


def take(manager, run="r1", user="u1", **kwargs):
    return manager.acquire(spec(), LeasePrincipal(user, run), wait_seconds=0, **kwargs)


def expire(manager, handle, *, metadata=False):
    lease = manager.status(handle)
    lease["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    manager._save(lease)
    if metadata:
        manager.provider.update_metadata(handle.provider_ref, manager._metadata(lease))


def retry_now(manager, handle):
    lease = manager.status(handle)
    lease["retry_at"] = 0
    manager._save(lease)


def test_restart_adopts_active_lease(env):
    make, provider, _, _ = env
    manager = make()
    handle = take(manager)
    manager.put_file(handle.lease_id, "/workspace/a.txt", b"survives")
    manager.close()
    restarted = make()
    restarted.recover()
    assert restarted.get(handle)["provider_ref"] == handle.provider_ref
    assert restarted.get_file(handle.lease_id, "/workspace/a.txt") == b"survives"
    assert provider.deletes == 0


def test_delete_failure_retains_ownership_and_retries_with_backoff(env):
    make, provider, _, _ = env
    manager = make()
    handle = take(manager)
    provider.fail_delete = True
    manager.release(handle)
    lease = manager.status(handle)
    assert lease["state"] == "releasing" and lease["release_attempts"] == 1
    manager.cleanup_once()
    assert provider.deletes == 1
    # Restart does not forget either the releasing state or its retry delay.
    manager.close()
    manager = make()
    manager.recover()
    assert provider.deletes == 1
    provider.fail_delete = False
    retry_now(manager, handle)
    manager.cleanup_once()
    assert manager.status(handle)["state"] == "released"
    assert provider.inspect(handle.provider_ref) is None


def test_missing_resource_is_a_typed_lease_failure(env):
    make, provider, _, _ = env
    manager = make()
    handle = take(manager)
    provider.release(handle.provider_ref)
    with pytest.raises(LeaseLostError) as exc:
        manager.exec(handle.lease_id, "echo should-not-run")
    assert exc.value.detail()["recoverable"] is True
    assert manager.status(handle)["state"] == "failed"


def test_redis_flush_rebuilds_from_labels_including_renewed_expiry(env):
    make, provider, _, flush = env
    manager = make()
    handle = take(manager)
    lease = manager.status(handle)
    lease["expires_at"] = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    manager._save(lease)
    renewed = manager.renew(handle)
    resource = provider.inspect(handle.provider_ref)
    for key in ("owner", "lease_id", "run_id", "user_id", "created_at"):
        assert resource.labels[f"ailab.{key}"] == renewed[key]
    assert resource.labels["ailab.expires_at"] == renewed["expires_at"]
    flush()
    manager.close()
    rebuilt = make()
    rebuilt.recover()
    assert rebuilt.get(handle)["expires_at"] == renewed["expires_at"]
    assert provider.deletes == 0


def test_flush_does_not_resurrect_releasing_lease(env):
    make, provider, _, flush = env
    manager = make()
    handle = take(manager)
    provider.fail_delete = True
    manager.release(handle)
    flush()
    manager.close()
    rebuilt = make()
    rebuilt.recover()
    assert rebuilt.status(handle)["state"] == "releasing"
    provider.fail_delete = False
    retry_now(rebuilt, handle)
    rebuilt.cleanup_once()
    assert rebuilt.status(handle)["state"] == "released"


def test_orphan_resource_deleted_without_touching_other_owners(env):
    make, provider, _, _ = env
    owned = provider.acquire("orphan", "image", labels={"ailab.owner": settings.broker_owner})
    other = provider.acquire("other", "image", labels={"ailab.owner": "another-group"})
    manager = make()
    manager.recover()
    assert provider.inspect(owned) is None
    assert provider.inspect(other) is not None


def test_expiry_without_heartbeat_releases_and_flush_cannot_revive_it(env):
    make, provider, _, flush = env
    manager = make()
    handle = take(manager)
    expire(manager, handle, metadata=True)
    with pytest.raises(LeaseExpiredError):
        manager.renew(handle)
    flush()
    manager.close()
    rebuilt = make()
    rebuilt.recover()
    assert provider.inspect(handle.provider_ref) is None
    assert rebuilt.status(handle)["state"] == "released"


def test_renew_is_capped_by_original_max_lifetime(env):
    make, _, _, _ = env
    manager = make()
    handle = take(manager)
    lease = manager.status(handle)
    lease["max_expires_at"] = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    manager._save(lease)
    renewed = manager.renew(handle)
    assert renewed["expires_at"] == renewed["max_expires_at"]
    with pytest.raises(Exception, match="original lease ttl"):
        manager.renew(handle, 61)


def test_fifo_queue_and_position(env):
    make, _, _, _ = env
    manager = make()
    first, occupied = take(manager), take(manager, "r2", "u2")
    queued1, queued2 = threading.Event(), threading.Event()
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(manager.acquire, spec(), LeasePrincipal("u3", "r3"), wait_seconds=2, on_queued=lambda _: queued1.set())
        assert queued1.wait(1)
        b = pool.submit(manager.acquire, spec(), LeasePrincipal("u4", "r4"), wait_seconds=2, on_queued=lambda _: queued2.set())
        assert queued2.wait(1)
        assert manager.admission_status("r3")["position"] == 1
        assert manager.admission_status("r4")["position"] == 2
        manager.release(first)
        acquired = a.result(timeout=1)
        assert not b.done()
        manager.release(acquired)
        assert b.result(timeout=1).lease_id
        manager.release(occupied)


def test_user_at_cap_does_not_block_other_users(env):
    make, _, _, _ = env
    manager = make()
    own, other = take(manager), take(manager, "r2", "u2")
    queued = threading.Event()
    with ThreadPoolExecutor(1) as pool:
        blocked = pool.submit(manager.acquire, spec(), LeasePrincipal("u1", "r3"), wait_seconds=2, on_queued=lambda _: queued.set())
        assert queued.wait(1)
        manager.release(other)
        newcomer = take(manager, "r4", "u3")
        assert not blocked.done()
        manager.release(own)
        assert blocked.result(timeout=1).lease_id
        manager.release(newcomer)


def test_reserved_render_slot_survives_full_general_pool_and_queue(env, monkeypatch):
    make, _, _, _ = env
    monkeypatch.setattr(settings, "sandbox_queue_limit", 1)
    manager = make()
    take(manager)
    take(manager, "r2", "u2")
    queued = threading.Event()
    with ThreadPoolExecutor(1) as pool:
        waiter = pool.submit(manager.acquire, spec(), LeasePrincipal("u3", "r3"), wait_seconds=0.2, on_queued=lambda _: queued.set())
        assert queued.wait(1)
        render = SandboxSpec(image="office", profile="render", memory=512 * 1024**2, ttl=20, max_lifetime=20)
        handle = manager.acquire(render, LeasePrincipal("system:worker", "render:job", system=True), wait_seconds=0)
        assert manager.get(handle)["spec"]["profile"] == "render"
        with pytest.raises(AdmissionRejected) as exc:
            manager.acquire(spec(), LeasePrincipal("u4", "r4"), wait_seconds=0.1)
        assert exc.value.reason == "queue_full"
        with pytest.raises(AdmissionTimeout):
            waiter.result(timeout=1)


def test_queue_deadline_is_a_typed_rejection(env):
    make, _, _, _ = env
    manager = make()
    take(manager)
    start = time.monotonic()
    with pytest.raises(AdmissionTimeout) as exc:
        manager.acquire(spec(), LeasePrincipal("u1", "another"), wait_seconds=0.02)
    assert exc.value.code == "sandbox_queue_deadline"
    assert manager.admission_status("another") == {"status": "absent"}
    assert time.monotonic() - start < 1


def test_second_live_broker_refuses_to_start(env):
    make, _, _, _ = env
    first = make()
    first.recover()
    with pytest.raises(BrokerOwnershipError):
        make().recover()


def test_cas_rejects_stale_writer_and_does_not_resurrect_release(env):
    make, _, _, _ = env
    manager = make()
    handle = take(manager)
    old = manager.get(handle)
    manager.release(handle)
    old["state"] = "active"
    with pytest.raises(LeaseConflictError):
        manager._save(old)
    assert manager.status(handle)["state"] == "released"


def test_interrupted_creation_is_deleted_instead_of_adopted(env):
    make, provider, _, flush = env
    manager = make()
    handle = take(manager)
    lease = manager.status(handle)
    lease["state"] = "creating"
    manager._save(lease)
    provider.update_metadata(handle.provider_ref, manager._metadata(lease))
    flush()
    manager.close()
    restarted = make()
    restarted.recover()
    assert provider.inspect(handle.provider_ref) is None
    assert restarted.status(handle)["state"] == "released"


def test_lease_disappears_during_exec_is_typed(env, monkeypatch):
    make, provider, _, _ = env
    manager = make()
    handle = take(manager)

    def disappear(*_):
        provider.release(handle.provider_ref)
        raise SandboxError("container gone during exec")

    monkeypatch.setattr(provider, "exec", disappear)
    with pytest.raises(LeaseLostError):
        manager.exec(handle.lease_id, "long command")


def test_reconcile_does_not_mark_allocation_after_list_as_missing(env, monkeypatch):
    make, provider, _, _ = env
    manager = make()
    manager.recover()
    listing = provider.list_owned
    allocated = []

    def race(owner):
        before = listing(owner)
        allocated.append(take(manager))
        return before

    monkeypatch.setattr(provider, "list_owned", race)
    manager.cleanup_once()
    assert manager.get(allocated[0])["state"] == "active"
    assert provider.deletes == 0


def test_unrelated_lease_cas_writes_do_not_conflict(env):
    _, _, store, _ = env
    barrier = threading.Barrier(8)

    def insert(i):
        barrier.wait(timeout=2)
        return store.cas(f"lease_{i}", None, {"lease_id": f"lease_{i}", "state": "creating"})

    with ThreadPoolExecutor(8) as pool:
        assert all(pool.map(insert, range(8)))
    assert len(store.live()) == 8
    same_barrier = threading.Barrier(8)

    def same(_):
        same_barrier.wait(timeout=2)
        return store.cas("same", None, {"lease_id": "same", "state": "active"})

    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(same, range(8))) == 1


def test_render_class_requires_trusted_system_principal(env):
    make, _, _, _ = env
    manager = make()
    render = SandboxSpec(image="office", profile="render", memory=512 * 1024**2, ttl=20, max_lifetime=20)
    with pytest.raises(AdmissionRejected) as exc:
        manager.acquire(render, LeasePrincipal("user", "model-chosen"), wait_seconds=0)
    assert exc.value.reason == "render_requires_short_system_job"


def test_live_redis_flush_is_rebuilt_by_background_reconciliation(env, monkeypatch):
    make, provider, _, flush = env
    monkeypatch.setattr(settings, "broker_lock_ttl_seconds", 3)
    monkeypatch.setattr(settings, "broker_reconcile_interval_seconds", 0.02)
    manager = make()
    handle = take(manager)
    manager.start_cleanup_thread()
    flush()
    assert not manager.is_ready()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not manager.is_ready():
        time.sleep(0.01)
    assert manager.is_ready()
    assert manager.get(handle)["state"] == "active"
    assert provider.deletes == 0
    # The periodic reconciler must remain alive after the lock was temporarily absent.
    expire(manager, handle)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and manager.status(handle)["state"] != "released":
        time.sleep(0.01)
    assert manager.status(handle)["state"] == "released"


def test_lock_loss_fences_existing_broker_before_resource_mutation(env):
    make, provider, store, _ = env
    manager = make()
    handle = take(manager)
    store.release_owner(manager._token)
    assert store.claim_owner("successor", 30)
    with pytest.raises(BrokerOwnershipError):
        manager.exec(handle.lease_id, "echo no")
    with pytest.raises(BrokerOwnershipError):
        manager.release(handle)
    assert provider.inspect(handle.provider_ref)


def test_container_killed_during_exec_exit_status_is_typed(env, monkeypatch):
    from ailab_platform.sandbox.base import ExecResult

    make, provider, _, _ = env
    manager = make()
    handle = take(manager)

    def killed(*_):
        provider.release(handle.provider_ref)
        return ExecResult(137, "", "killed", 0)

    monkeypatch.setattr(provider, "exec", killed)
    with pytest.raises(LeaseLostError):
        manager.exec(handle.lease_id, "long command")


def test_mock_cancels_process_group_without_releasing_lease(env):
    make, provider, _, _ = env
    manager = make()
    handle = take(manager)
    with ThreadPoolExecutor(1) as pool:
        execution = pool.submit(manager.exec, handle.lease_id, "sleep 30", 30)
        deadline = time.monotonic() + 1
        try:
            while time.monotonic() < deadline:
                with provider._process_lock:
                    if provider._processes.get(handle.provider_ref):
                        break
                time.sleep(0.001)
            assert manager.cancel_exec(handle) is True
            assert execution.result(timeout=1).exit_code != 0
            assert manager.get(handle)["state"] == "active"
        finally:
            provider.cancel_exec(handle.provider_ref)


def test_rebuild_in_progress_is_unavailable_not_false_lease_loss(env):
    make, _, store, _ = env
    manager = make()
    handle = take(manager)
    record = manager.get(handle)
    manager._recovering = True
    assert store.delete(handle.lease_id, record["version"])
    with pytest.raises(BrokerOwnershipError):
        manager.get(handle)
    with pytest.raises(BrokerOwnershipError):
        manager.status(handle)
    with pytest.raises(BrokerOwnershipError):
        manager.release(handle)
    manager._recovering = False
    manager.cleanup_once()
    assert manager.get(handle)["state"] == "active"
