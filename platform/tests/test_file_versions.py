"""Immutable lineages, quota, trash and preservation of historical references."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from ailab_platform.api.main import app
from ailab_platform.api.reaper import purge_expired_trash
from ailab_platform.config import Settings, settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import Artifact, FileObject, Run
from ailab_platform.quota import library_bytes
from ailab_platform.storage import get_storage
from tests.run_claims import attempt_headers, delivery_headers
from tests.test_conversations import _other_user
from tests.test_library import batch, listed, ready
from tests.test_projects import AUTH, SERVICE, project


@pytest.fixture()
def api():
    with TestClient(app) as client:
        client.headers.update(_other_user(client))
        yield client


def version(api, parent, data=b"new"):
    response = api.post("/v1/files", params={"parent_id": parent["id"]}, files={"upload": (parent["filename"], data, "text/plain")})
    assert response.status_code == 201, response.text
    file = response.json()
    assert api.patch(f"/internal/files/{file['id']}", headers=SERVICE, json={"status": "ready"}).status_code == 200
    return file


def history(api, file):
    response = api.get(f"/v1/files/{file['id']}/versions")
    assert response.status_code == 200, response.text
    return response.json()


def test_eleventh_version_keeps_tombstone_and_original_references(api):
    first = ready(api, "history.txt", b"original")
    run = api.post("/v1/runs", json={"prompt": "read", "file_ids": [first["id"]]}).json()
    with SessionLocal() as db:
        old_key = db.get(FileObject, first["id"]).storage_key
        db.add(Artifact(run_id=run["id"], file_id=first["id"]))
        db.commit()
    current = first
    for _ in range(10):
        current = version(api, current)
    versions = history(api, first)
    assert [f["version"] for f in versions] == list(range(11, 0, -1))
    assert {f["lineage_id"] for f in versions} == {first["id"]}
    assert all(f["versions"] == 10 for f in versions)
    assert [f["id"] for f in versions if f["is_head"]] == [current["id"]]
    assert versions[-1]["purged"] and not any(f["purged"] for f in versions[:-1])
    assert api.get(f"/v1/files/{first['id']}/content").json() == {"detail": "version purged"}
    assert api.get(f"/v1/files/{first['id']}/content").status_code == 410
    assert not get_storage().exists(old_key)
    assert api.get(f"/v1/runs/{run['id']}").json()["artifacts"][0]["file_id"] == first["id"]
    assert api.get(f"/v1/runs/{run['id']}").json()["input_files"][0]["file_id"] == first["id"]
    assert api.post(f"/v1/files/{first['id']}/restore-version").status_code == 410


def test_restore_version_is_a_new_copy_and_history_does_not_change(api):
    first = ready(api, "history.txt", b"old bytes")
    second = version(api, first, b"new bytes")
    before = history(api, first)
    with SessionLocal() as db:
        owner = db.get(FileObject, first["id"]).user_id
        used = library_bytes(db, owner)
    response = api.post(f"/v1/files/{first['id']}/restore-version")
    assert response.status_code == 201, response.text
    restored = response.json()
    assert restored["id"] not in {first["id"], second["id"]}
    assert restored["version"] == 3 and restored["versions"] == 3 and restored["is_head"]
    assert api.get(restored["download_url"]).content == b"old bytes"
    after = history(api, first)[1:]
    for old, new in zip(before, after):
        assert {k: old[k] for k in ("id", "version", "sha256", "created_at")} == {
            k: new[k] for k in ("id", "version", "sha256", "created_at")
        }
    with SessionLocal() as db:
        assert library_bytes(db, owner) == used + len(b"old bytes")
        assert db.get(FileObject, restored["id"]).storage_key != db.get(FileObject, first["id"]).storage_key
    assert api.post(f"/v1/files/{restored['id']}/restore-version").status_code == 409
    assert api.get(f"/v1/files/{first['id']}/content").content == b"old bytes"


def test_restore_version_quota_and_copy_failure_roll_back(api, monkeypatch):
    first = ready(api)
    second = version(api, first)
    monkeypatch.setattr(settings, "library_quota_bytes", 1)
    assert api.post(f"/v1/files/{first['id']}/restore-version").status_code == 413
    monkeypatch.setattr(settings, "library_quota_bytes", 1000)

    def fail(*args):
        raise RuntimeError("copy failed")

    monkeypatch.setattr(get_storage(), "copy", fail)
    with pytest.raises(RuntimeError, match="copy failed"):
        api.post(f"/v1/files/{first['id']}/restore-version")
    assert len(history(api, first)) == 2
    assert history(api, first)[0]["id"] == second["id"]


def test_list_search_ids_and_projects_only_show_live_heads(api):
    p = project(api)
    first = ready(api, "report.txt")
    second = version(api, first)
    api.patch(f"/v1/files/{first['id']}", json={"project_id": p["id"], "starred": True})
    assert listed(api) == listed(api, q="report") == listed(api, category="starred") == [second["id"]]
    assert listed(api, ids=first["id"]) == []
    assert api.get(f"/v1/projects/{p['id']}").json()["files"] == 1
    assert api.delete(f"/v1/files/{first['id']}").status_code == 204
    assert listed(api) == listed(api, q="report") == listed(api, ids=second["id"]) == []
    assert api.get(f"/v1/projects/{p['id']}").json()["files"] == 0
    assert listed(api, trashed=True) == [second["id"]]
    trash = history(api, first)
    assert all(f["deleted_at"] and f["purge_at"] for f in trash)
    assert datetime.fromisoformat(trash[0]["purge_at"]) - datetime.fromisoformat(trash[0]["deleted_at"]) == timedelta(days=30)
    assert api.get(f"/v1/files/{first['id']}/content").content == b"data"
    assert api.post("/v1/runs", json={"prompt": "read", "file_ids": [first["id"]]}).status_code == 400
    assert api.get("/v1/files/archive", params={"ids": second["id"]}).status_code == 404
    assert api.post(f"/v1/files/{first['id']}/restore").json()["id"] == second["id"]
    assert listed(api) == [second["id"]]


@pytest.mark.parametrize("trash_action", ["delete", "trash"])
def test_batch_restore_and_purge_apply_to_the_whole_lineage(api, trash_action):
    first = ready(api)
    second = version(api, first)
    assert batch(api, [first["id"]], "purge").status_code == 409
    assert batch(api, [first["id"], second["id"]], trash_action).json() == {"count": 2}
    assert all(f["deleted_at"] for f in history(api, first))
    assert batch(api, [second["id"]], "restore").json() == {"count": 1}
    assert not any(f["deleted_at"] for f in history(api, first))
    assert batch(api, [second["id"]], trash_action).status_code == 200
    assert batch(api, [first["id"]], "purge").json() == {"count": 1}
    assert api.get(f"/v1/files/{first['id']}").status_code == 404
    assert api.get(f"/v1/files/{second['id']}").status_code == 404


@pytest.mark.parametrize("path,method", [("versions", "get"), ("restore", "post"), ("restore-version", "post")])
def test_version_routes_hide_foreign_files(api, path, method):
    first = ready(api)
    version(api, first)
    assert getattr(api, method)(f"/v1/files/{first['id']}/{path}", headers=AUTH).status_code == 404


@pytest.mark.parametrize("action", ["trash", "restore", "purge"])
def test_batch_new_actions_remain_atomic_and_owned(api, action):
    first = ready(api)
    api.delete(f"/v1/files/{first['id']}")
    for ids in ([first["id"], "missing"], [first["id"]] * 201):
        assert batch(api, ids, action).status_code in (404, 422)
    assert api.get(f"/v1/files/{first['id']}").json()["deleted_at"]
    assert batch(api, [first["id"]], action).status_code == 200


def test_trash_quota_is_freed_only_after_purge(api, monkeypatch):
    first = ready(api, data=b"12345")
    monkeypatch.setattr(settings, "library_quota_bytes", 5)
    api.delete(f"/v1/files/{first['id']}")
    assert api.post("/v1/files", files={"upload": ("new.txt", b"1")}).status_code == 413
    assert batch(api, [first["id"]], "purge").status_code == 200
    assert api.post("/v1/files", files={"upload": ("new.txt", b"12345")}).status_code == 201


def test_project_trash_restore_retains_specific_version_references(api):
    p = project(api)
    first = ready(api)
    api.patch(f"/v1/files/{first['id']}", json={"project_id": p["id"]})
    second = version(api, first)
    conv = api.post("/v1/conversations", json={"project_id": p["id"]}).json()
    run = api.post(f"/v1/conversations/{conv['id']}/messages", json={"prompt": "read", "file_ids": [first["id"]]}).json()
    api.delete(f"/v1/projects/{p['id']}")
    rows = history(api, first)
    assert all(f["project_id"] is None and f["trashed_from_project_id"] == p["id"] for f in rows)
    assert api.get(f"/v1/runs/{run['id']}").json()["input_files"][0]["file_id"] == first["id"]
    assert api.post(f"/v1/files/{first['id']}/restore").json()["project_id"] is None
    assert listed(api) == [second["id"]]


def test_reaper_is_bounded_rechecks_expiry_and_is_idempotent(api):
    first, second, active = ready(api), ready(api), ready(api)
    api.delete(f"/v1/files/{first['id']}")
    api.delete(f"/v1/files/{second['id']}")
    with SessionLocal() as db:
        now = datetime.now(UTC)
        assert purge_expired_trash(db, now) == 0
        db.execute(update(FileObject).where(FileObject.id.in_([first["id"], second["id"]])).values(purge_at=now - timedelta(seconds=1)))
        db.commit()
        assert purge_expired_trash(db, now, batch_size=1) == 1
        assert purge_expired_trash(db, now, batch_size=1) == 1
        assert purge_expired_trash(db, now) == 0
    assert api.get(f"/v1/files/{active['id']}").status_code == 200


def test_empty_trash_is_owned_counts_lineages_and_is_idempotent(api):
    first = ready(api)
    second = version(api, first)
    active = ready(api)
    api.delete(f"/v1/files/{first['id']}")
    assert api.delete("/v1/trash", headers=AUTH).status_code == 200
    assert api.get(f"/v1/files/{second['id']}").status_code == 200
    assert api.delete("/v1/trash").json() == {"count": 1}
    assert api.delete("/v1/trash").json() == {"count": 0}
    assert api.get(f"/v1/files/{active['id']}").status_code == 200


def test_concurrent_artifact_commits_allocate_consecutive_versions(api):
    conv = api.post("/v1/conversations", json={}).json()
    run = api.post(f"/v1/conversations/{conv['id']}/messages", json={"prompt": "write"}).json()
    owner = attempt_headers(api, run["id"])
    api.post(f"/internal/runs/{run['id']}/status", headers=owner, json={"status": "running"})
    barrier = Barrier(5)

    def commit(i):
        data = f"version {i}".encode()
        key = f"staging/{run['id']}/{i}"
        get_storage().put_bytes(key, data)
        body = {
            "run_id": run["id"],
            "staging_key": key,
            "filename": "out.txt",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        barrier.wait(timeout=10)
        response = TestClient(app).post("/internal/artifacts", headers=delivery_headers(owner, run["id"], key), json=body)
        assert response.status_code == 200, response.text
        return response.json()

    with ThreadPoolExecutor(max_workers=5) as pool:
        artifacts = list(pool.map(commit, range(5)))
    rows = history(api, {"id": artifacts[0]["file_id"]})
    assert [f["version"] for f in rows] == [5, 4, 3, 2, 1]
    assert len({f["lineage_id"] for f in rows}) == 1
    assert len(listed(api)) == 1
    with SessionLocal() as db:
        assert db.get(Run, run["id"]).conversation_id == conv["id"]
        assert len(list(db.scalars(select(Artifact).where(Artifact.run_id == run["id"])))) == 5


@pytest.mark.parametrize("values", [{"max_file_versions": 0}, {"trash_days": 0}, {"trash_days": -1}])
def test_retention_settings_validate_ranges(values):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **values)


def test_trash_cursor_and_stable_order(api):
    files = [ready(api) for _ in range(3)]
    for f in files:
        api.delete(f"/v1/files/{f['id']}")
    first = api.get("/v1/files", params={"trashed": True, "limit": 2}).json()
    second = api.get("/v1/files", params={"trashed": True, "limit": 2, "cursor": first[-1]["id"]}).json()
    assert [f["id"] for f in first + second] == [f["id"] for f in reversed(files)]
    assert api.get("/v1/files", params={"cursor": "missing"}).status_code == 404
    assert api.get("/v1/files", params={"trashed": True, "cursor": files[0]["id"]}, headers=AUTH).status_code == 404


def test_purge_cleans_unregistered_nested_derived_objects(api):
    first = ready(api)
    key = f"derived/{first['id']}/preview/pages/1.png"
    storage = get_storage()
    storage.put_bytes(key, b"preview")
    api.delete(f"/v1/files/{first['id']}")
    assert storage.exists(key)
    assert batch(api, [first["id"]], "purge").status_code == 200
    assert not storage.exists(key)


def test_rejected_trash_still_counts_until_purged(api):
    first = ready(api, data=b"12345")
    api.patch(f"/internal/files/{first['id']}", headers=SERVICE, json={"status": "rejected"})
    api.delete(f"/v1/files/{first['id']}")
    with SessionLocal() as db:
        owner = db.get(FileObject, first["id"]).user_id
        assert library_bytes(db, owner) == 5
    batch(api, [first["id"]], "purge")
    with SessionLocal() as db:
        assert library_bytes(db, owner) == 0


def test_project_delete_covers_more_than_one_batch_and_already_trashed(api):
    p = project(api)
    first = ready(api)
    api.patch(f"/v1/files/{first['id']}", json={"project_id": p["id"]})
    api.delete(f"/v1/files/{first['id']}")
    with SessionLocal() as db:
        owner = db.get(FileObject, first["id"]).user_id
        db.add_all(
            [
                FileObject(
                    id=f"test-many-{i}-{p['id']}", user_id=owner, project_id=p["id"], filename="many.txt", storage_key=f"many/{p['id']}/{i}"
                )
                for i in range(205)
            ]
        )
        db.commit()
    assert api.delete(f"/v1/projects/{p['id']}").status_code == 204
    with SessionLocal() as db:
        files = list(db.scalars(select(FileObject).where(FileObject.trashed_from_project_id == p["id"])))
        assert len(files) == 206 and all(f.project_id is None and f.deleted_at and f.purge_at for f in files)
    assert api.delete("/v1/trash").json() == {"count": 206}


def test_restore_copies_derived_objects_including_nested_metadata(api, monkeypatch):
    first = ready(api)
    prefix = f"derived/{first['id']}"
    text_key, preview_key = f"{prefix}/text.txt", f"{prefix}/preview/pages/1.png"
    for key in (text_key, preview_key):
        get_storage().put_bytes(key, b"derived bytes")
    api.patch(f"/internal/files/{first['id']}", headers=SERVICE, json={"meta": {"text_key": text_key, "preview": {"pages": [preview_key]}}})
    second = version(api, first)
    monkeypatch.setattr(settings, "max_file_versions", 2)
    restored = api.post(f"/v1/files/{first['id']}/restore-version").json()
    assert restored["is_head"] and restored["version"] == 3
    for key in (restored["meta"]["text_key"], restored["meta"]["preview"]["pages"][0]):
        assert key.startswith(f"derived/{restored['id']}/")
        assert get_storage().get_bytes(key) == b"derived bytes"
    assert not get_storage().exists(text_key) and not get_storage().exists(preview_key)
    assert api.get(f"/v1/files/{first['id']}/content").status_code == 410
    assert api.get(f"/v1/files/{second['id']}/content").status_code == 200


def test_restore_pending_version_preserves_scan_state_and_trash(api):
    first = ready(api)
    api.patch(f"/internal/files/{first['id']}", headers=SERVICE, json={"status": "pending"})
    second = version(api, first)
    api.delete(f"/v1/files/{first['id']}")
    response = api.post(f"/v1/files/{first['id']}/restore-version")
    assert response.status_code == 201, response.text
    restored = response.json()
    assert restored["status"] == "pending" and restored["deleted_at"] and restored["purge_at"]
    assert listed(api) == [] and listed(api, trashed=True) == [restored["id"]]
    assert api.get(restored["download_url"]).status_code == 409
    assert api.post(f"/v1/files/{second['id']}/restore").json()["id"] == restored["id"]


def test_empty_trash_does_not_chase_new_deletions_forever(api, monkeypatch):
    from ailab_platform.api import routes_files

    first, later = ready(api), ready(api)
    api.delete(f"/v1/files/{first['id']}")
    cleanup = routes_files.delete_storage_objects
    calls = []

    def delete_and_trash_another(keys):
        cleanup(keys)
        if not calls:
            calls.append(True)
            assert api.delete(f"/v1/files/{later['id']}").status_code == 204

    monkeypatch.setattr(routes_files, "delete_storage_objects", delete_and_trash_another)
    assert api.delete("/v1/trash").json() == {"count": 1}
    assert api.get(f"/v1/files/{later['id']}").json()["deleted_at"]


def test_same_name_in_later_turn_versions_only_within_its_conversation(api):
    from tests.test_projects import artifact

    def output(conversation, data):
        run = api.post(f"/v1/conversations/{conversation['id']}/messages", json={"prompt": "write"}).json()
        api.post(f"/internal/runs/{run['id']}/status", headers=attempt_headers(api, run["id"]), json={"status": "running"})
        result = artifact(api, run["id"], data)
        api.post(f"/internal/runs/{run['id']}/status", headers=attempt_headers(api, run["id"]), json={"status": "succeeded"})
        return api.get(f"/v1/files/{result['file_id']}").json()

    conv = api.post("/v1/conversations", json={}).json()
    first = output(conv, b"turn one")
    second = output(conv, b"turn two")
    other = output(api.post("/v1/conversations", json={}).json(), b"other conversation")
    assert first["lineage_id"] == second["lineage_id"] and second["version"] == 2
    assert other["lineage_id"] != first["lineage_id"] and other["version"] == 1
    assert api.get(first["download_url"]).content == b"turn one"


def test_purge_between_page_id_selection_and_detail_fetch_is_ignored(api):
    from sqlalchemy import event

    from ailab_platform.db import engine

    first = ready(api)
    purged = []

    def purge_after_page_ids(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT files.id") and "LIMIT" in statement and not purged:
            purged.append(True)
            assert api.delete(f"/v1/files/{first['id']}").status_code == 204
            assert batch(api, [first["id"]], "purge").status_code == 200

    event.listen(engine, "after_cursor_execute", purge_after_page_ids)
    try:
        assert listed(api) == []
        assert purged
    finally:
        event.remove(engine, "after_cursor_execute", purge_after_page_ids)


def test_purge_with_storage_down_leaves_durable_intents_that_the_lifecycle_finishes(api, monkeypatch):
    """Rows go in the transaction; objects that could not be removed then are retried, not leaked."""
    from ailab_platform.api.reaper import drain_object_deletions
    from ailab_platform.models import LifecycleDeletion

    file = ready(api, "doomed.txt", b"doomed")
    with SessionLocal() as db:
        key = db.get(FileObject, file["id"]).storage_key
    storage = get_storage()
    assert storage.exists(key)
    assert api.delete(f"/v1/files/{file['id']}").status_code == 204
    original = type(storage).delete
    monkeypatch.setattr(type(storage), "delete", lambda self, k: (_ for _ in ()).throw(OSError("storage offline")))
    assert api.delete("/v1/trash").status_code == 200
    assert storage.exists(key)  # still there: the immediate delete failed
    with SessionLocal() as db:
        assert db.get(FileObject, file["id"]) is None
        assert db.scalar(select(LifecycleDeletion).where(LifecycleDeletion.key == key)) is not None
    monkeypatch.setattr(type(storage), "delete", original)
    with SessionLocal() as db:
        for _ in range(5):
            drain_object_deletions(db, storage)
        assert db.scalar(select(LifecycleDeletion).where(LifecycleDeletion.key == key)) is None
    assert not storage.exists(key)
