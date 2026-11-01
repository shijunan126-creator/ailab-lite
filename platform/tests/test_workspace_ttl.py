from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ailab_platform.api.reaper import purge_expired_workspaces
from ailab_platform.api.routes_internal import workspace_alive, workspace_key
from ailab_platform.storage import LocalStorage
from tests.run_claims import attempt_headers
from tests.test_ledger_protocol import api  # noqa: F401

pytestmark = pytest.mark.usefixtures("api")


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


def test_ttl_zero_keeps_snapshots_instead_of_deleting_them_all(tmp_path, monkeypatch) -> None:
    from ailab_platform.config import settings

    monkeypatch.setattr(settings, "workspace_ttl_days", 0)
    storage = LocalStorage(tmp_path)
    storage.put_bytes(workspace_key("run_any"), b"x")
    long_ago = (datetime(2026, 1, 1, tzinfo=UTC)).timestamp()
    os.utime(tmp_path / "workspaces" / "run_any.tgz", (long_ago, long_ago))
    assert purge_expired_workspaces(storage=storage, now=datetime(2026, 6, 1, tzinfo=UTC)) == 0
    assert storage.exists(workspace_key("run_any"))
    old = SimpleNamespace(result={"workspace_saved": True}, finished_at=datetime(2026, 1, 1, tzinfo=UTC), created_at=None)
    assert workspace_alive(old, datetime(2026, 6, 1, tzinfo=UTC)) is True


def test_an_expired_workspace_is_reported_to_the_agent(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from ailab_platform.api.main import app
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import Run

    auth, service = {"Authorization": "Bearer test-token"}, {"X-Service-Token": "test-service"}
    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=auth, json={}).json()
        one = c.post(f"/v1/conversations/{conv['id']}/messages", headers=auth, json={"prompt": "做个表"}).json()
        c.post(f"/internal/runs/{one['id']}/status", headers=attempt_headers(c, one["id"]), json={"status": "running"})
        old = (datetime.now(UTC) - timedelta(days=4)).isoformat()
        c.post(
            f"/internal/runs/{one['id']}/status",
            headers=attempt_headers(c, one["id"]),
            json={"status": "succeeded", "summary": "ok", "result": {"workspace_saved": True, "workspace_saved_at": old}},
        )
        two = c.post(f"/v1/conversations/{conv['id']}/messages", headers=auth, json={"prompt": "改一下"}).json()
        got = c.get(f"/internal/runs/{two['id']}", headers=service).json()
        assert got["workspace_restore_key"] is None and got["workspace_expired"] is True
        with SessionLocal() as db:  # within the window: restored, nothing reported
            run = db.get(Run, one["id"])
            run.result = {**run.result, "workspace_saved_at": datetime.now(UTC).isoformat()}
            db.commit()
        got = c.get(f"/internal/runs/{two['id']}", headers=service).json()
        assert got["workspace_restore_key"] == workspace_key(one["id"]) and got["workspace_expired"] is False


@pytest.mark.parametrize("lazy", [True, False])
def test_workspace_gc_streams_objects_without_snapshot_sized_queries(monkeypatch, lazy):
    from sqlalchemy import event

    from ailab_platform.api import reaper
    from ailab_platform.config import settings
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import Run, RunStatus, User

    monkeypatch.setattr(settings, "gc_batch_size", 7)
    now = datetime.now(UTC)
    protected = {f"workspaces/gc_live_{i}/current.tgz" for i in range(23)}
    with SessionLocal() as db:
        owner = db.query(User.id).first()[0]
        db.add_all(
            Run(
                id=f"gc_live_{i}",
                user_id=owner,
                prompt="gc",
                status=RunStatus.INTERRUPTED,
                runtime_meta={"workspace": {"key": key}},
            )
            for i, key in enumerate(sorted(protected))
        )
        db.commit()
        engine = db.get_bind()

    queries = []
    caller = threading.get_ident()

    def record(conn, cursor, statement, parameters, context, executemany):
        # The engine is shared with background loops left by earlier app instances; count only this GC call.
        if threading.get_ident() != caller:
            return
        if "runs" in statement and statement.lstrip().upper().startswith("SELECT"):
            queries.append((statement, len(parameters)))

    class StreamingStorage:
        deleted = 0

        def objects(self):
            for key in sorted(protected):
                yield SimpleNamespace(key=key, modified_at=now - timedelta(days=5))
            for i in range(2000):
                # Reading ahead before deletion catches list(iterator), even for
                # backends that supply a lazy object page.
                assert not lazy or self.deleted == i
                yield SimpleNamespace(key=f"workspaces/old_{i}.tgz", modified_at=now - timedelta(days=5))

        def delete(self, key):
            assert key not in protected
            self.deleted += 1

    storage = StreamingStorage()
    monkeypatch.setattr(reaper, "_page", lambda *_: storage.objects() if lazy else list(storage.objects()))
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert purge_expired_workspaces(storage, now) == 2000
    finally:
        event.remove(engine, "before_cursor_execute", record)
        with SessionLocal() as db:
            db.query(Run).filter(Run.id.like("gc_live_%")).delete(synchronize_session=False)
            db.commit()
    assert queries
    assert all(count <= 4 and "LIMIT" in statement.upper() for statement, count in queries)
    assert any(" > " in statement for statement, _ in queries)
