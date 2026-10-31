from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ailab_platform.broker.render import LibraryRenderer
from ailab_platform.broker.service import LeaseError, LeaseLostError
from ailab_platform.config import settings
from ailab_platform.domain.admission import LeasePrincipal
from tests.test_broker_lease import _manager


def warmed(monkeypatch):
    manager, provider, _ = _manager()
    execute = provider.exec
    monkeypatch.setattr(
        provider,
        "exec",
        lambda ref, command, timeout: SimpleNamespace(exit_code=0) if "warmup.fodt" in command else execute(ref, command, timeout),
    )
    spec = LibraryRenderer(manager)._spec()
    lease_id = manager.create_standby(spec)
    return manager, provider, spec, lease_id


def test_single_use_adoption_is_atomic_and_hidden(monkeypatch):
    manager, provider, spec, lease_id = warmed(monkeypatch)
    assert manager.store.get(lease_id)["state"] == "standby"
    with pytest.raises(LeaseLostError):
        manager.exec(lease_id, "true")
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


def test_disabled_and_failed_warmup(monkeypatch):
    manager, provider, _ = _manager()
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
    monkeypatch.setattr(provider, "exec", lambda *args: SimpleNamespace(exit_code=0))
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


def test_replenishment_waits_for_render(monkeypatch):
    manager, _, _ = _manager()
    manager._ensure_started()
    manager._start_background = True
    calls = []
    monkeypatch.setattr(manager, "create_standby", lambda spec: calls.append(spec) or (_ for _ in ()).throw(LeaseError("stop")))
    with manager.render_activity():
        manager.request_prewarm()
        assert not calls
    for thread in __import__("threading").enumerate():
        if thread.name == "render-prewarm":
            thread.join(2)
    assert len(calls) == 1
