"""Skill attestation and immutable workspace recovery on disposable sandbox providers."""

from ailab_platform.adapters.lease_store_memory import MemoryLeaseStore
from ailab_platform.broker.routes_skills import _attested, verify_lease_index
from ailab_platform.broker.service import LeaseManager
from ailab_platform.config import settings
from ailab_platform.skill_index import checked_index
from ailab_platform.storage import LocalStorage
from tests.contracts.test_sandbox_lifecycle import provider  # noqa: F401
from tests.fakes import FakeRedis
from tests.test_workspace_previews import _Api


def test_lease_attests_current_skills_and_restores_its_checkpoint_workspace(provider, tmp_path):  # noqa: F811
    api = _Api()
    manager = LeaseManager(
        provider=provider,
        platform=api,
        storage=LocalStorage(tmp_path / "objects"),
        redis_client=FakeRedis(),
        store=MemoryLeaseStore(),
        start_background=False,
    )
    _attested.clear()
    try:
        first = manager.create("run_skill_probe")
        proof = verify_lease_index(manager, first["lease_id"])
        assert proof["verified"] and proof["index"] == checked_index(settings.skills_path)
        assert proof["provider_ref"] == first["provider_ref"]
        if provider.name != "mock":
            # The runtime user must not be able to change what the cached proof describes.
            result = manager.exec(first["lease_id"], "printf tampered >> /opt/skills/xlsx/SKILL.md", 10)
            assert result.exit_code != 0
        manager.put_file(first["lease_id"], "/workspace/outputs/kept.txt", b"checkpoint workspace")
        snapshot = manager.snapshot(first["lease_id"])
        manager.release(first["lease_id"])
        api.runs["run_skill_resume"] = {"workspace_restore_key": snapshot["key"]}
        second = manager.create("run_skill_resume")
        assert second["restored"] is True
        assert manager.get_file(second["lease_id"], "/workspace/outputs/kept.txt") == b"checkpoint workspace"
        assert verify_lease_index(manager, second["lease_id"])["provider_ref"] != proof["provider_ref"]
    finally:
        for lease in manager.active_leases():
            manager.release(lease["lease_id"])
        manager.close()
        _attested.clear()
