"""Deterministic request-order races, executed against memory and the real Redis script."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ailab_platform.api import view_state
from ailab_platform.config import settings


@pytest.fixture(params=["memory", "redis"])
def store(request, monkeypatch):
    client = None
    if request.param == "redis":
        url = os.environ.get("AILAB_TEST_REDIS_URL")
        if not url:
            pytest.skip("AILAB_TEST_REDIS_URL not set")
        import redis

        client = redis.Redis.from_url(url)
        client.ping()
        monkeypatch.setattr(settings, "redis_url", url)
    monkeypatch.setattr(settings, "queue_backend", request.param)
    owner, conv = uuid4().hex, uuid4().hex
    now = [1000.0]
    monkeypatch.setattr(view_state, "time", SimpleNamespace(time=lambda: now[0]))
    yield owner, conv, now
    if client:
        client.delete(view_state._key(owner, conv))
        client.close()
    with view_state._lock:
        view_state._memory.pop(view_state._key(owner, conv), None)


def state(view="v1", at="p:1"):
    return {"view_id": view, "file_id": "f1", "version": "hash", "at": at}


def test_close_fences_both_inflight_and_not_yet_received_report(store):
    user, conv, _ = store
    ticket = view_state.begin(user, conv, "v1", 1)
    view_state.delete(user, conv, "v1")
    view_state.delete(user, conv, "v1")
    assert not view_state.commit(user, conv, state(), ticket)
    assert view_state.begin(user, conv, "v1", 2) is None
    view_state.delete(user, conv, "unreceived")
    assert view_state.begin(user, conv, "unreceived", 1) is None
    assert view_state.views(user, conv) == []
    # Reopening uses a fresh frame/view identity.
    fresh = view_state.begin(user, conv, "v2", 1)
    assert view_state.commit(user, conv, state("v2"), fresh)


def test_newer_sequence_wins_even_when_old_authorization_finishes_last(store):
    user, conv, _ = store
    old = view_state.begin(user, conv, "v1", 1)
    newer = view_state.begin(user, conv, "v1", 2)
    assert view_state.commit(user, conv, state(at="p:2"), newer)
    assert not view_state.commit(user, conv, state(), old)
    assert view_state.begin(user, conv, "v1", 1) is None
    assert view_state.begin(user, conv, "v1", 2) is None
    assert view_state.views(user, conv) == [state(at="p:2")]


def test_expired_fence_and_active_ttl_never_allow_old_commit(store):
    user, conv, now = store
    old = view_state.begin(user, conv, "v1", 1)
    now[0] += view_state.WRITE_SECONDS + 1
    assert not view_state.commit(user, conv, state(), old)
    newer = view_state.begin(user, conv, "v1", 2)
    assert view_state.commit(user, conv, state(), newer)
    now[0] += view_state.TTL + 1
    assert view_state.views(user, conv) == []
    fresh = view_state.begin(user, conv, "v1", 3)
    assert not view_state.commit(user, conv, state(), old)
    assert view_state.commit(user, conv, state(), fresh)


def test_closed_records_are_bounded_without_evicting_live_fences(store):
    user, conv, now = store
    for i in range(view_state.MAX_RECORDS):
        view_state.delete(user, conv, f"closed-{i}")
    with pytest.raises(ValueError):
        view_state.begin(user, conv, "new", 1)
    with pytest.raises(ValueError):
        view_state.delete(user, conv, "new")
    assert view_state.begin(user, conv, "closed-0", 1) is None
    assert view_state.views(user, conv) == []
    now[0] += view_state.TTL + 1
    assert view_state.begin(user, conv, "new", 1)


def test_active_limit_and_legacy_sequence_compatibility(store):
    user, conv, _ = store
    for i in range(view_state.MAX_VIEWS):
        ticket = view_state.begin(user, conv, f"v{i}")
        assert view_state.commit(user, conv, state(f"v{i}"), ticket)
    with pytest.raises(ValueError):
        view_state.begin(user, conv, "overflow", 1)
    # Old clients may omit sequence; arrival-order reservations still fence completion order.
    old = view_state.begin(user, conv, "v0")
    newer = view_state.begin(user, conv, "v0")
    assert not view_state.commit(user, conv, state("v0"), old)
    assert view_state.commit(user, conv, state("v0", "p:2"), newer)


def test_rolling_upgrade_reads_old_snapshot_and_can_close_it(store):
    import json

    user, conv, now = store
    key = view_state._key(user, conv)
    old = [now[0] + view_state.TTL, state()]
    if settings.queue_backend == "memory":
        with view_state._lock:
            view_state._memory[key] = {"v1": old}
    else:
        with view_state._redis() as client:
            client.hset(key, "v1", json.dumps(old))
    assert view_state.views(user, conv) == [state()]
    ticket = view_state.begin(user, conv, "v1", 1)
    assert view_state.commit(user, conv, state(at="p:2"), ticket)
    view_state.delete(user, conv, "v1")
    assert view_state.views(user, conv) == []
    assert not view_state.commit(user, conv, state(), ticket)
