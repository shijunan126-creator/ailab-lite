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
