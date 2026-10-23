"""Restart, lost Redis records, and externally killed resources on disposable real providers."""

import os
import uuid
from urllib.parse import urlsplit

import pytest
import redis

from ailab_platform.adapters.lease_store_redis import RedisLeaseStore
from ailab_platform.broker.service import LeaseLostError, LeaseManager
from ailab_platform.config import settings
from ailab_platform.domain.admission import LeasePrincipal
from ailab_platform.sandbox.base import SandboxSpec
from ailab_platform.sandbox.docker import DockerSandbox
from ailab_platform.sandbox.opensandbox import OpenSandboxProvider


@pytest.mark.parametrize("backend", ["docker", "opensandbox"])
def test_real_provider_restart_redis_loss_and_external_kill(backend, monkeypatch):
    url = os.getenv("AILAB_TEST_REDIS_URL")
    if not url:
        pytest.skip("AILAB_TEST_REDIS_URL must name a throwaway Redis")
    if backend == "docker":
        if os.getenv("AILAB_TEST_DOCKER") != "1":
            pytest.skip("AILAB_TEST_DOCKER=1 required")
        provider = DockerSandbox()
    else:
        endpoint = os.getenv("AILAB_TEST_OSB_URL")
        if not endpoint:
            pytest.skip("AILAB_TEST_OSB_URL required")
        pytest.importorskip("opensandbox")
        parsed = urlsplit(endpoint)
        monkeypatch.setattr(settings, "osb_domain", parsed.netloc)
        monkeypatch.setattr(settings, "osb_protocol", parsed.scheme)
        provider = OpenSandboxProvider()
    monkeypatch.setattr(settings, "broker_owner", "fault-" + uuid.uuid4().hex)
    client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
    store = RedisLeaseStore(client, settings.broker_owner)
    managers = []
    handle = None

    def start():
        manager = LeaseManager(
            provider=provider, platform=object(), storage=object(), redis_client=client, store=store, start_background=False
        )
        managers.append(manager)
        return manager

    try:
        first = start()
        handle = first.acquire(
            SandboxSpec(image=settings.sandbox_image, ttl=120, max_lifetime=240), LeasePrincipal("qa", "fault-run"), wait_seconds=0
        )
        first.put_file(handle.lease_id, "/workspace/recovery.txt", b"preserved")
        first.close()
        restarted = start()
        assert restarted.get_file(handle.lease_id, "/workspace/recovery.txt") == b"preserved"
        restarted.close()
        # Remove this owner's Redis state, leaving the real resource intact.
        client.delete(store.key, store.live_key, store.owner_key)
        recovered = start()
        assert recovered.get_file(handle.lease_id, "/workspace/recovery.txt") == b"preserved"
        provider.release(handle.provider_ref)
        recovered.cleanup_once()
        with pytest.raises(LeaseLostError):
            recovered.get_file(handle.lease_id, "/workspace/recovery.txt")
    finally:
        for manager in managers:
            manager.close()
        if handle:
            provider.release(handle.provider_ref)
        client.delete(store.key, store.live_key, store.owner_key)
        client.close()
        if backend == "opensandbox":
            provider._manager.close()
