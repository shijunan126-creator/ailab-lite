from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from ailab_platform.broker.render import LibraryRenderer
from ailab_platform.broker.service import AdmissionRejected, AdmissionTimeout, LeaseError, LeaseLostError
from ailab_platform.config import settings
from ailab_platform.domain.admission import LeasePrincipal
from tests.test_broker_lease import _manager


def warmed(monkeypatch):
    manager, provider, _ = _manager()
    execute = provider.exec
    monkeypatch.setattr(
        provider,
        "exec",
        lambda ref, command, timeout: (
            SimpleNamespace(exit_code=0, stdout='{"tmp_used":0}')
            if "warmup.fodt" in command or "render_sandbox_health.py" in command
            else execute(ref, command, timeout)
        ),
    )
    spec = LibraryRenderer(manager)._spec()
    lease_id = manager.create_standby(spec)
    return manager, provider, spec, lease_id


def complete(manager, handle, run_id, success=True):
    generation = manager.store.get(handle.lease_id)["render_generation"]
    return manager.finish_render(handle, run_id=run_id, generation=generation, success=success)


def test_single_use_adoption_is_atomic_and_hidden(monkeypatch):
    manager, provider, spec, lease_id = warmed(monkeypatch)
    assert manager.store.get(lease_id)["state"] == "standby"
    with pytest.raises(LeaseLostError):
        manager.exec(lease_id, "true")
    with pytest.raises(LeaseLostError):
        manager.status(lease_id)
    principal = LeasePrincipal("system:preview", "preview:job", system=True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        handles = list(executor.map(lambda _: manager.adopt_standby(spec, principal, lambda lease: None), range(2)))
    handle = next(h for h in handles if h)
    assert sum(h is not None for h in handles) == 1
    assert handle.lease_id == lease_id
    assert manager.get(lease_id)["run_id"] == principal.run_id
    manager.release(handle)
    assert provider.released == [lease_id]
    assert manager.adopt_standby(spec, principal, lambda lease: None) is None


def test_idle_and_restart_release_standby(monkeypatch):
    manager, provider, _, lease_id = warmed(monkeypatch)
    lease = manager.store.get(lease_id)
    lease["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    manager._save(lease)
    manager.cleanup_once()
    assert lease_id in provider.released
    manager, provider, _, lease_id = warmed(monkeypatch)
    manager._recovering = True
    manager.cleanup_once()
    manager._recovering = False
    assert lease_id in provider.released


def test_shutdown_releases_render_without_deleting_work(monkeypatch):
    manager, provider, _, lease_id = warmed(monkeypatch)
    work = manager.create("work-run")
    manager.close()
    assert lease_id in provider.released
    assert provider.inspect(work["provider_ref"]) is not None


def test_bound_idle_only_reuses_for_the_same_user(monkeypatch):
    manager, provider, spec, lease_id = warmed(monkeypatch)
    first = manager.adopt_standby(spec, LeasePrincipal("user_a", "preview:a", system=True), lambda lease: None)
    generation = manager.store.get(lease_id)["render_generation"]
    assert first and complete(manager, first, "preview:a")
    assert manager.store.get(lease_id)["state"] == "idle"
    with pytest.raises(LeaseLostError):
        manager.status(lease_id)
    assert manager.adopt_standby(spec, LeasePrincipal("user_b", "preview:b", system=True), lambda lease: None) is None
    second = manager.adopt_standby(spec, LeasePrincipal("user_a", "preview:a", system=True), lambda lease: None)
    assert second and second.lease_id == lease_id
    assert not manager.finish_render(first, run_id="preview:a", generation=generation, success=True)
    assert manager.store.get(lease_id)["state"] == "active"
    assert not complete(manager, second, "preview:a", success=False)
    assert lease_id in provider.released


def test_on_demand_reuses_only_authorized_user_even_with_prewarm_disabled(monkeypatch):
    manager, _, spec, lease_id = warmed(monkeypatch)
    monkeypatch.setattr(settings, "render_prewarm", 0)
    first, source = manager.acquire_render(spec, LeasePrincipal("user_a", "preview:a", system=True), lambda lease: None)
    assert source == "warm" and first.lease_id == lease_id
    assert complete(manager, first, "preview:a")
    again, source = manager.acquire_render(spec, LeasePrincipal("user_a", "preview:b", system=True), lambda lease: None)
    assert source == "warm" and again.lease_id == lease_id
    manager.release(again)


def test_failed_cleanup_never_returns_render_to_idle(monkeypatch):
    manager, provider, spec, lease_id = warmed(monkeypatch)
    handle = manager.adopt_standby(spec, LeasePrincipal("user_a", "preview:a", system=True), lambda lease: None)
    monkeypatch.setattr(provider, "exec", lambda *args: SimpleNamespace(exit_code=1))
    assert handle and not complete(manager, handle, "preview:a")
    assert lease_id in provider.released


def test_reclaim_never_preempts_busy_or_same_user_idle(monkeypatch):
    manager, provider, spec, lease_id = warmed(monkeypatch)
    owner = LeasePrincipal("user_a", "preview:a", system=True)
    other = LeasePrincipal("user_b", "preview:b", system=True)
    handle = manager.adopt_standby(spec, owner, lambda lease: None)
    assert handle and not manager._reclaim_for(other, spec, "capacity")
    assert complete(manager, handle, owner.run_id)
    assert not manager._reclaim_for(owner, spec, "capacity")
    assert manager._reclaim_for(other, spec, "capacity")
    assert lease_id in provider.released


def test_reuse_job_limit_releases_the_sandbox(monkeypatch):
    monkeypatch.setattr(settings, "render_reuse_max_jobs", 2)
    manager, provider, spec, lease_id = warmed(monkeypatch)
    first = manager.adopt_standby(spec, LeasePrincipal("user_a", "preview:a", system=True), lambda lease: None)
    assert first and complete(manager, first, "preview:a")
    second = manager.adopt_standby(spec, LeasePrincipal("user_a", "preview:b", system=True), lambda lease: None)
    assert second and not complete(manager, second, "preview:b")
    assert lease_id in provider.released


def test_render_lifecycle_does_not_retain_records_after_1000_cycles(monkeypatch):
    import gc
    import tracemalloc

    manager, provider, spec, first = warmed(monkeypatch)
    manager.release(first)
    provider.released.clear()
    provider.acquired_ttl.clear()
    tracemalloc.start()
    try:
        gc.collect()
        before = tracemalloc.get_traced_memory()[0]
        midpoint = before
        for i in range(1000):
            lease_id = manager.create_standby(spec)
            handle = manager.adopt_standby(spec, LeasePrincipal(f"user_{i}", f"preview:{i}", system=True), lambda lease: None)
            assert handle and complete(manager, handle, f"preview:{i}")
            manager.release(lease_id)
            provider.released.clear()
            provider.acquired_ttl.clear()
            if i == 499:
                gc.collect()
                midpoint = tracemalloc.get_traced_memory()[0]
                midpoint_snapshot = tracemalloc.take_snapshot()
        gc.collect()
        after = tracemalloc.get_traced_memory()[0]
        assert after - before < 6 * 1024 * 1024
        growth = tracemalloc.take_snapshot().compare_to(midpoint_snapshot, "lineno")[:5]
        # pathlib interns unique MockSandbox path components; CPython may resize
        # that global table once by about 3.7 MiB during this measurement.
        assert after - midpoint < 5 * 1024 * 1024, str(growth)
        assert not manager.store.all()
        assert not manager._render_birth and not manager._activity and not manager._operations and not manager.admission.queue
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize("lose_record", [False, True])
def test_recovery_releases_busy_render_instead_of_adopting(monkeypatch, lose_record):
    manager, provider, spec, lease_id = warmed(monkeypatch)
    handle = manager.adopt_standby(spec, LeasePrincipal("user_a", "preview:job", system=True), lambda lease: None)
    assert handle is not None
    if lose_record:
        lease = manager.store.get(lease_id)
        assert manager.store.delete(lease_id, lease["version"])
    manager._recovering = True
    try:
        manager.cleanup_once()
    finally:
        manager._recovering = False
    assert lease_id in provider.released
    assert not manager.active_leases()


def test_disabled_and_failed_warmup(monkeypatch):
    manager, _, _ = _manager()
    spec = LibraryRenderer(manager)._spec()
    monkeypatch.setattr(settings, "render_prewarm", 0)
    with pytest.raises(LeaseError, match="disabled"):
        manager.create_standby(spec)
    assert not manager.active_leases()


def test_standby_provider_lifetime_covers_idle_and_job(monkeypatch):
    manager, provider, _ = _manager()
    original = provider.acquire
    specs = []

    def acquire(*args, **kwargs):
        specs.append(kwargs["spec"])
        return original(*args, **kwargs)

    monkeypatch.setattr(provider, "acquire", acquire)
    monkeypatch.setattr(provider, "exec", lambda *args: SimpleNamespace(exit_code=0, stdout='{"tmp_used":0}'))
    spec = LibraryRenderer(manager)._spec()
    lease_id = manager.create_standby(spec)
    assert specs[0].max_lifetime >= settings.render_prewarm_idle_seconds + spec.max_lifetime
    assert manager.store.get(lease_id)["spec"]["max_lifetime"] == spec.max_lifetime
    manager.release(lease_id)
    monkeypatch.setattr(settings, "render_prewarm", 1)
    monkeypatch.setattr(provider, "exec", lambda *args: SimpleNamespace(exit_code=1))
    with pytest.raises(LeaseError, match="warmup"):
        manager.create_standby(spec)
    assert not manager.active_leases()


def test_separate_jobs_use_separate_sandboxes(monkeypatch):
    manager, provider, spec, first = warmed(monkeypatch)
    principal = LeasePrincipal("system:preview", "preview:a", system=True)
    handle = manager.adopt_standby(spec, principal, lambda lease: manager.put_input(lease, "/workspace/inputs/a.txt", b"private"))
    manager.release(handle)
    second = manager.create_standby(spec)
    assert second != first
    assert not (provider.root / second / "workspace/inputs/a.txt").exists()
    manager.release(second)


def test_replenishment_waits_for_render(monkeypatch):
    manager, _, _ = _manager()
    manager._ensure_started()
    manager._start_background = True
    calls = []
    called = Event()

    def fail(spec):
        calls.append(spec)
        called.set()
        raise LeaseError("stop")

    monkeypatch.setattr(manager, "create_standby", fail)
    with manager.render_activity():
        manager.request_prewarm()
        assert not calls
    assert called.wait(2)
    manager.request_prewarm()
    assert not manager._stop.wait(0.2)
    assert len(calls) == 1
    manager.close()


def test_waiting_render_precedes_replenishment(monkeypatch):
    manager, _, _ = _manager()
    manager._ensure_started()
    manager._start_background = True
    waiting = Event()
    entered = Event()
    release = Event()
    calls = []
    monkeypatch.setattr(manager, "create_standby", lambda spec: calls.append("warm") or (_ for _ in ()).throw(LeaseError("stop")))

    def second():
        waiting.set()
        with manager.render_activity():
            entered.set()
            release.wait(2)

    with manager.render_activity():
        thread = Thread(target=second)
        thread.start()
        assert waiting.wait(1)
        with manager._condition:
            assert manager._condition.wait_for(lambda: manager._render_waiters == 1, timeout=1)
    assert entered.wait(1)
    assert not calls
    release.set()
    thread.join(2)
    manager.close()


def test_render_scheduler_prioritizes_then_round_robins_users():
    manager, _, _ = _manager()
    order = []

    def run(owner, rank):
        with manager.render_activity(owner, priority=rank, wait_seconds=5):
            order.append(owner)

    with manager.render_activity("a", priority=1):
        threads = [Thread(target=run, args=item) for item in [("a", 1), ("a", 1), ("b", 1), ("c", 0)]]
        for thread in threads:
            thread.start()
        with manager._condition:
            assert manager._condition.wait_for(lambda: len(manager._render_tickets) == 4, timeout=3)
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert order == ["c", "b", "a", "a"]
    assert not manager._render_tickets and manager._render_waiters == 0


def test_render_scheduler_bounds_queue_and_cleans_expired_tickets(monkeypatch):
    manager, _, _ = _manager()
    monkeypatch.setattr(settings, "render_queue_limit", 1)
    waiting = Event()
    errors = []

    def queued():
        waiting.set()
        try:
            with manager.render_activity("b", wait_seconds=0.2):
                pytest.fail("busy conversion was preempted")
        except AdmissionTimeout:
            errors.append("deadline")

    with manager.render_activity("a"):
        thread = Thread(target=queued)
        thread.start()
        assert waiting.wait(1)
        with manager._condition:
            assert manager._condition.wait_for(lambda: len(manager._render_tickets) == 1, timeout=1)
        with pytest.raises(AdmissionRejected, match="render_queue_full"):
            with manager.render_activity("c", wait_seconds=0):
                pass
        thread.join(2)
        assert errors == ["deadline"] and not manager._render_tickets
