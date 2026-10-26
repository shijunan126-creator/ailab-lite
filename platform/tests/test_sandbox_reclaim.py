"""A user's sandbox allowance stretches with free capacity; idle sandboxes are reclaimed (workspace saved first)."""

from __future__ import annotations

import threading
import time

import pytest

from ailab_platform.broker.service import AdmissionRejected, LeaseManager
from ailab_platform.config import settings
from ailab_platform.domain.admission import LeasePrincipal
from ailab_platform.storage import LocalStorage

from .test_broker_lease import _ApiStub, _manager_with


@pytest.fixture
def pool(monkeypatch, tmp_path) -> LeaseManager:
    """Nine general slots and one render slot; a user may hold up to three."""
    for name, value in {
        "max_sandboxes": 10,
        "sandbox_render_reserved_slots": 1,
        "sandbox_memory_bytes": 1024**3,
        "sandbox_render_memory_bytes": 1024**3,
        "sandbox_total_memory_bytes": 64 * 1024**3,
        "sandbox_max_per_user": 3,
        "sandbox_burst_watermark": 0.8,
        "sandbox_evict_idle_seconds": 60,
        "sandbox_preempt_utilization": 0.95,
    }.items():
        monkeypatch.setattr(settings, name, value)
    manager, _, _ = _manager_with(_ApiStub({}, []))
    manager.storage = LocalStorage(tmp_path / "objects")
    return manager


def take(manager: LeaseManager, user: str, run: str, *, wait: float = 0) -> dict:
    manager.acquire(manager.default_spec(), LeasePrincipal(user, run), wait_seconds=wait, ensure_active=lambda: None)
    return next(x for x in manager.active_leases() if x["run_id"] == run)


def age(manager: LeaseManager, lease: dict, seconds: float = 3600) -> None:
    manager._activity[lease["lease_id"]] = time.monotonic() - seconds


def wait_released(manager: LeaseManager, lease: dict) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = manager.status(lease["lease_id"])
        if current["state"] == "released":
            return current
        time.sleep(0.05)
    raise AssertionError("the reclaimed sandbox was never deleted")


def test_a_second_sandbox_is_allowed_while_the_pool_has_room(pool):
    assert [take(pool, "user_a", f"a{i}")["state"] for i in range(3)] == ["active"] * 3
    with pytest.raises(AdmissionRejected):  # the hard ceiling is three
        take(pool, "user_a", "a3")


def test_the_allowance_shrinks_to_one_when_the_pool_fills(pool):
    take(pool, "user_a", "a0")
    for i in range(6):  # seven of nine general slots busy
        take(pool, f"user_{i}", f"r{i}")
    with pytest.raises(AdmissionRejected) as busy:
        take(pool, "user_a", "a1")  # an eighth would be 89%, past the 80% watermark
    assert busy.value.reason == "queue_deadline"
    assert take(pool, "user_new", "n0")["state"] == "active"  # a first sandbox is never withheld by the watermark


def test_a_user_at_the_cap_reclaims_their_own_longest_idle_sandbox_and_it_is_saved_first(pool):
    first, second, third = (take(pool, "user_a", f"a{i}") for i in range(3))
    pool._provider_call("put_file", first["provider_ref"], "/workspace/outputs/draft.md", b"draft")
    age(pool, first, 7200)
    age(pool, second, 3600)  # idle too, but not the longest
    age(pool, third, 5)
    started = threading.Event()
    result: dict = {}

    def ask():
        started.set()
        result["lease"] = take(pool, "user_a", "a3", wait=15)

    worker = threading.Thread(target=ask)
    worker.start()
    started.wait()
    worker.join(timeout=15)
    gone = wait_released(pool, first)
    assert gone["reason"] == "evicted_idle" and gone["eviction_done"] is True
    assert result["lease"]["state"] == "active"
    assert pool.status(second["lease_id"])["state"] == "active"
    saved = gone["eviction_snapshot"]
    assert saved["key"].startswith(f"workspaces/{first['run_id']}/") and saved["size"] > 0
    assert pool.storage.exists(saved["key"])


def test_nothing_is_reclaimed_while_every_sandbox_was_used_recently(pool):
    for i in range(3):
        take(pool, "user_a", f"a{i}")
    with pytest.raises(AdmissionRejected):
        take(pool, "user_a", "a3", wait=1.5)
    assert {x["state"] for x in pool.active_leases()} == {"active"}


def test_a_running_operation_protects_its_sandbox_however_old_the_last_one_is(pool):
    leases = [take(pool, "user_a", f"a{i}") for i in range(3)]
    for lease in leases:
        age(pool, lease)
    pool._operations[leases[0]["lease_id"]] = 1
    pool._operations[leases[1]["lease_id"]] = 1
    pool._operations[leases[2]["lease_id"]] = 1
    with pytest.raises(AdmissionRejected):
        take(pool, "user_a", "a3", wait=1.5)
    assert {x["state"] for x in pool.active_leases()} == {"active"}


def test_heartbeats_do_not_count_as_activity(pool):
    lease = take(pool, "user_a", "a0")
    age(pool, lease)
    pool.renew(lease["lease_id"])
    assert pool.idle_seconds(lease) > 3000
    pool.exec(lease["lease_id"], "true")
    assert pool.idle_seconds(lease) < 5


def test_other_users_are_only_reclaimed_once_the_pool_is_nearly_full(pool):
    leases = [take(pool, f"user_{i}", f"r{i}") for i in range(7)]
    for lease in leases:
        age(pool, lease)
    principal = LeasePrincipal("user_new", "n0")
    assert pool._pick_victim(principal, "capacity", pool.active_leases()) is None  # 7 of 9 is far from full
    leases += [take(pool, f"user_{i}", f"r{i}") for i in (7, 8)]
    for lease in leases:
        age(pool, lease)
    assert pool._pick_victim(principal, "capacity", pool.active_leases()) is not None


def test_a_full_pool_gives_up_a_burst_sandbox_before_anyones_only_one(pool, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_burst_watermark", 1.0)  # let the test build a full pool in one go
    mine = [take(pool, "user_a", f"a{i}") for i in range(3)]
    singles = [take(pool, f"user_{i}", f"s{i}") for i in range(6)]
    for lease in mine + singles:
        age(pool, lease)
    age(pool, singles[0], 99999)  # the single sandbox that has been idle longest
    victim = pool._pick_victim(LeasePrincipal("user_new", "n0"), "capacity", pool.active_leases())
    assert victim["user_id"] == "user_a"


def test_a_sandbox_being_saved_is_not_deleted_by_a_release_from_its_run(pool):
    lease = take(pool, "user_a", "a0")
    stored = pool.store.get(lease["lease_id"])
    stored.update(state="releasing", reason="evicted_idle", retry_at=time.time() + 600)
    pool._save(stored)
    pool.release(lease["lease_id"])
    assert pool.provider.released == []


def test_a_mid_turn_heartbeat_shields_a_sandbox_past_the_base_floor(pool):
    first, second = take(pool, "user_a", "a0"), take(pool, "user_a", "a1")
    age(pool, first, 100)  # past the 60s base floor, inside the 3x mid-turn floor
    age(pool, second, 100)
    pool.renew(first["lease_id"])  # its run is mid-turn: silent, thinking, not dead
    victim = pool._pick_victim(LeasePrincipal("user_a", "a2"), "user_limit", pool.active_leases())
    assert victim["lease_id"] == second["lease_id"]


def test_a_stopped_heartbeat_leaves_the_base_floor(pool):
    lease = take(pool, "user_a", "a0")
    age(pool, lease, 100)
    pool.renew(lease["lease_id"])
    pool._renewed[lease["lease_id"]] -= 10 * settings.runtime_heartbeat_seconds  # the agent died
    assert pool._pick_victim(LeasePrincipal("user_a", "a1"), "user_limit", pool.active_leases())["lease_id"] == lease["lease_id"]


def test_a_fresh_heartbeat_is_still_reclaimed_after_three_floors(pool):
    lease = take(pool, "user_a", "a0")
    age(pool, lease, 4 * 60 * settings.runtime_heartbeat_seconds)
    pool.renew(lease["lease_id"])
    assert pool._pick_victim(LeasePrincipal("user_a", "a1"), "user_limit", pool.active_leases())["lease_id"] == lease["lease_id"]
