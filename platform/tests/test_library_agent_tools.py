"""Owned READY library access, bounded Unicode pages and real indexed query plans."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from ailab_platform.api.main import app
from ailab_platform.api.routes_library_tools import Search, library_query
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, Run
from ailab_platform.storage import get_storage
from tests.run_claims import SERVICE, attempt_headers
from tests.test_conversations import AUTH, _status


def test_library_search_and_text_pages_are_owned_ready_and_project_first():
    with TestClient(app) as c:
        project = c.post("/v1/projects", headers=AUTH, json={"name": "lib" + uuid.uuid4().hex[:8]}).json()
        conv = c.post("/v1/conversations", headers=AUTH, json={"project_id": project["id"]}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "read", "mode": "chat"}).json()
        with SessionLocal() as db:
            owner = db.get(Run, run["id"]).user_id
            files = []
            for index, properties in enumerate(
                [
                    {"project_id": project["id"]},
                    {},
                    {"purpose": "skill"},
                    {"status": "pending"},
                ]
            ):
                file = FileObject(
                    user_id=owner,
                    filename=f"libtool-{uuid.uuid4().hex}-{index}.txt",
                    storage_key="not parsed",
                    status="ready",
                    meta={"text_key": f"derived/{uuid.uuid4().hex}/text.txt"},
                )
                for k, v in properties.items():
                    setattr(file, k, v)
                db.add(file)
                files.append(file)
            db.commit()
            ids = [f.id for f in files]
            key = files[0].meta["text_key"]
        text_ = "中文🍎" * 12000
        get_storage().put_bytes(key, text_.encode())
        url = f"/internal/runs/{run['id']}/library"
        found = c.post(url + "/search", headers=SERVICE, json={"query": "libtool-"}).json()["files"]
        assert found[0]["file_id"] == ids[0]
        assert set(ids[:2]) <= {f["file_id"] for f in found} and not set(ids[2:]) & {f["file_id"] for f in found}
        assert set(found[0]) == {"file_id", "filename", "content_type", "size", "project", "updated_at", "summary"}
        assert c.post(url + "/search", headers=SERVICE, json={"query": "", "limit": 21}).status_code == 422
        first = c.post(url + "/read", headers=SERVICE, json={"file_id": ids[0]}).json()
        assert first["text"] == text_[:30000] and first["next_offset"] == 30000 and first["truncated"]
        last = c.post(url + "/read", headers=SERVICE, json={"file_id": ids[0], "offset": 30000}).json()
        assert first["text"] + last["text"] == text_ and last["next_offset"] is None
        for id_ in ids[2:]:
            assert c.post(url + "/read", headers=SERVICE, json={"file_id": id_}).status_code == 404
        assert c.post(url + "/read", headers=AUTH, json={"file_id": ids[0]}).status_code == 403
        _status(c, run["id"], "running")
        assert c.post(url + "/authorize-copy", headers=attempt_headers(c, run["id"]), json={"file_id": ids[0]}).status_code == 422
        # A foreign run cannot use the first user's file, even knowing its id.
        from tests.test_conversations import _other_user

        other = c.post("/v1/runs", headers=_other_user(c), json={"prompt": "foreign"}).json()
        assert c.post(f"/internal/runs/{other['id']}/library/read", headers=SERVICE, json={"file_id": ids[0]}).status_code == 404


def test_library_search_query_uses_user_index():
    from ailab_platform.db import init_db

    init_db()
    with SessionLocal() as db:
        # Verify index suitability on both dialects; small test databases may choose a seq scan by cost.
        query = library_query("user-for-explain", None, Search(query="name", limit=20))
        sql = str(query.compile(db.bind, compile_kwargs={"literal_binds": True}))
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SET LOCAL enable_seqscan = off"))
            rows = db.execute(text("EXPLAIN " + sql))
        else:
            rows = db.execute(text("EXPLAIN QUERY PLAN " + sql))
        plan = " ".join(str(tuple(row)) for row in rows)
        assert "ix_files_" in plan and ("user_id" in plan or "user_created" in plan)


def test_work_copy_grant_is_bound_to_run_and_broker_streams_into_inputs(monkeypatch):
    from pathlib import Path

    from ailab_platform.broker.main import copy_library_file
    from tests.test_broker_lease import _ApiStub, _manager_with

    with TestClient(app) as c:
        run = c.post("/v1/runs", headers=AUTH, json={"prompt": "copy", "mode": "work"}).json()
        _status(c, run["id"], "running")
        with SessionLocal() as db:
            owner = db.get(Run, run["id"]).user_id
            file = FileObject(user_id=owner, filename=f"copy-{uuid.uuid4().hex}.txt", storage_key="unused", status="ready")
            db.add(file)
            db.commit()
            id_, filename = file.id, file.filename
        url = f"/internal/runs/{run['id']}/library/authorize-copy"
        assert c.post(url, headers=attempt_headers(c, run["id"]), json={"file_id": id_}).status_code == 200
        with SessionLocal() as db:
            assert id_ in db.get(Run, run["id"]).runtime_meta["library_file_ids"]
        api = _ApiStub({id_: {"status": "ready", "filename": filename}}, [])
        manager, provider, _ = _manager_with(api)
        lease = manager.create(run["id"])
        monkeypatch.setattr("ailab_platform.broker.main.get_manager", lambda: manager)
        bindings = []

        def download(file_id, target, **context):
            assert file_id == id_
            bindings.append(context)
            Path(target).write_bytes(b"library bytes")

        monkeypatch.setattr(api, "download_file", download)
        result = copy_library_file(lease["lease_id"], {"file_id": id_})
        assert result["path"] == f"/workspace/inputs/{filename}"
        assert bindings[0]["run_id"] == run["id"]
        assert provider.get_file(lease["sandbox_id"], result["path"]) == b"library bytes"
        import pytest
        from fastapi import HTTPException

        for path in ("/workspace/outputs/escape", "/workspace/inputs/../../etc/a"):
            with pytest.raises(HTTPException) as exc:
                copy_library_file(lease["lease_id"], {"file_id": id_, "path": path})
            assert exc.value.status_code == 422


from tests.test_service_identity import identities as identities  # noqa: E402


def test_copy_grant_is_enforced_for_real_broker_identity(identities):
    with TestClient(app) as c:
        run = c.post("/v1/runs", headers=AUTH, json={"prompt": "copy", "mode": "work"}).json()
        _status(c, run["id"], "running")
        with SessionLocal() as db:
            owner = db.get(Run, run["id"]).user_id
            file = FileObject(user_id=owner, filename=f"bound-{uuid.uuid4().hex}.txt", storage_key="unused", status="ready")
            db.add(file)
            db.commit()
            id_ = file.id
        get = f"/internal/files/{id_}?run_id={run['id']}"
        assert c.get(get, headers=identities["broker"]).status_code == 404
        authorize = f"/internal/runs/{run['id']}/library/authorize-copy"
        assert c.post(authorize, headers=identities["worker"], json={"file_id": id_}).status_code == 403
        header = {**identities["agent"], "X-Run-Attempt": attempt_headers(c, run["id"])["X-Run-Attempt"]}
        assert c.post(authorize, headers=header, json={"file_id": id_}).status_code == 200
        assert c.get(get, headers=identities["broker"]).status_code == 200
        foreign = c.post("/v1/runs", headers=AUTH, json={"prompt": "other turn"}).json()
        _status(c, foreign["id"], "running")
        assert c.get(f"/internal/files/{id_}?run_id={foreign['id']}", headers=identities["broker"]).status_code == 404
