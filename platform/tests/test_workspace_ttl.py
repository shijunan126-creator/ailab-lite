from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from ailab_platform.api.reaper import purge_expired_workspaces
from ailab_platform.api.routes_internal import workspace_alive, workspace_key
from ailab_platform.storage import LocalStorage


def test_expired_workspace_objects_are_deleted(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    storage.put_bytes(workspace_key("run_old"), b"old")
    storage.put_bytes(workspace_key("run_new"), b"new")

    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    old = (now - timedelta(days=4)).timestamp()
    new = (now - timedelta(hours=1)).timestamp()
    os.utime(tmp_path / "workspaces" / "run_old.tgz", (old, old))
    os.utime(tmp_path / "workspaces" / "run_new.tgz", (new, new))

    assert purge_expired_workspaces(storage=storage, now=now) == 1
    assert not storage.exists(workspace_key("run_old"))
    assert storage.exists(workspace_key("run_new"))


def test_workspace_restore_is_only_alive_for_three_days() -> None:
    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    fresh = SimpleNamespace(
        result={"workspace_saved": True, "workspace_saved_at": (now - timedelta(days=2)).isoformat()},
        finished_at=None,
        created_at=now,
    )
    expired = SimpleNamespace(
        result={"workspace_saved": True, "workspace_saved_at": (now - timedelta(days=3, seconds=1)).isoformat()},
        finished_at=None,
        created_at=now,
    )
    legacy = SimpleNamespace(
        result={"workspace_saved": True},  # snapshots from before workspace_saved_at existed
        finished_at=now - timedelta(days=1),
        created_at=now,
    )
    legacy_expired = SimpleNamespace(
        result={"workspace_saved": True},
        finished_at=now - timedelta(days=4),
        created_at=now,
    )

    assert workspace_alive(fresh, now) is True
    assert workspace_alive(expired, now) is False
    assert workspace_alive(legacy, now) is True
    assert workspace_alive(legacy_expired, now) is False
