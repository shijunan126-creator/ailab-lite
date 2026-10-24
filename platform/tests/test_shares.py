"""Share authorization, immutable path snapshots and shared delivery (both DB dialects)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from ailab_platform.api.main import app
from ailab_platform.api.routes_shares import live_conditions, received
from ailab_platform.db import SessionLocal
from ailab_platform.models import Artifact, AuditLog, Conversation, FileObject, Run, Share, User, utcnow
from ailab_platform.storage import get_storage
from tests.test_conversations import AUTH, _finish, _other_user


@pytest.fixture
def shared():
    with TestClient(app) as c:
        recipient, stranger = _other_user(c), _other_user(c)
        rid = c.get("/v1/me", headers=recipient).json()["id"]
        conv = c.post("/v1/conversations", headers=AUTH, json={"title": "分享测试"}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "公开问题"}).json()
        _finish(c, run["id"], "公开回答", thinking="秘密思考", tool_steps=[{"secret": "秘密工具"}])
        with SessionLocal() as db:
            owner = db.get(Run, run["id"]).user_id
            files = []
            for i in range(3):
                key = f"share-test/{uuid.uuid4().hex}"
                get_storage().put_bytes(key, b"public")
                f = FileObject(
                    user_id=owner,
                    filename=f"share-{i}-{uuid.uuid4().hex}.txt",
                    status="ready",
                    storage_key=key,
                    size=6,
                    sha256="digest",
                    meta={"text_key": key, "thumb_key": key, "preview": {"status": "ready", "kind": "text", "text": "text.txt"}},
                )
                db.add(f)
                files.append(f)
            db.flush()
            db.add(Artifact(run_id=run["id"], file_id=files[0].id))
            db.get(Run, run["id"]).input_file_ids = [files[1].id]
            db.commit()
            fids = [f.id for f in files]
        response = c.post(
            "/v1/shares",
            headers=AUTH,
            json={"kind": "conversation", "conversation_id": conv["id"], "audience": "people", "user_ids": [rid]},
        )
        assert response.status_code == 201, response.text
        yield c, response.json(), recipient, stranger, conv["id"], run["id"], owner, fids


def test_non_recipient_revoke_deactivated_owner_and_audit(shared):
    c, share, recipient, stranger, _, _, owner, fids = shared
    url = f"/v1/shares/{share['id']}"
    assert c.get(url + "/view", headers=stranger).status_code == 404
    assert c.get(url + "/view", headers=recipient).status_code == 200
    assert c.patch(url, headers=recipient, json={"audience": "everyone"}).status_code == 404
    with SessionLocal() as db:
        db.get(User, owner).disabled_at = utcnow()
        db.commit()
    try:
        assert c.get(url + "/view", headers=recipient).status_code == 404
        assert c.get(url + f"/files/{fids[0]}/content", headers=recipient).status_code == 404
    finally:
        with SessionLocal() as db:
            db.get(User, owner).disabled_at = None
            db.commit()
    assert c.patch(url, headers=AUTH, json={"audience": "everyone"}).status_code == 200
    assert c.get(url + "/view", headers=stranger).status_code == 200
    assert c.delete(url, headers=AUTH).status_code == 204
    assert c.get(url + "/view", headers=recipient).status_code == 404
    assert c.get(url + f"/files/{fids[0]}/content", headers=recipient).status_code == 404
    with SessionLocal() as db:
        assert set(db.scalars(select(AuditLog.action).where(AuditLog.target_id == share["id"]))) == {
            "shares.create",
            "shares.update",
            "shares.revoke",
        }


def test_snapshot_and_refresh_excludes_future_turns_attachments_and_foreign_artifacts(shared):
    c, share, recipient, _, cid, first, _owner, fids = shared
    url = f"/v1/shares/{share['id']}"
    later = c.post(f"/v1/conversations/{cid}/messages", headers=AUTH, json={"prompt": "未来问题"}).json()
    _finish(c, later["id"], "未来回答")
    with SessionLocal() as db:
        db.add(Artifact(run_id=later["id"], file_id=fids[2]))
        db.commit()
    data = c.get(url + "/view", headers=recipient).json()
    assert [t["id"] for t in data["turns"]] == [first]
    assert data["turns"][0]["answer"] == "公开回答"
    assert len(data["turns"][0]["attachments"]) == 1
    assert data["turns"][0]["attachments"][0]["filename"].startswith("share-1-")
    assert "秘密" not in str(data) and "thinking" not in str(data) and "tool_steps" not in str(data)
    artifact = data["turns"][0]["artifacts"][0]
    assert artifact["content_url"].startswith("/api/v1/shares/")
    # Same-origin Portal rewrites /api/v1 to /v1; no owner URL or presigned redirect is involved.
    assert c.get(artifact["content_url"].replace("/api/v1/", "/v1/", 1), headers=recipient).content == b"public"
    for f in fids[1:]:
        for action in ["content", "preview", "preview/text.txt", "thumbnail"]:
            assert c.get(url + f"/files/{f}/{action}", headers=recipient).status_code == 404
    assert c.get(url + f"/files/{fids[0]}/preview", headers=recipient).status_code == 200
    asset = c.get(url + f"/files/{fids[0]}/preview/text.txt", headers=recipient)
    assert asset.content == b"public" and asset.headers["cache-control"] == "private, no-store"
    assert (
        c.get(url + f"/files/{fids[0]}/preview/text.txt", headers={**recipient, "If-None-Match": asset.headers["etag"]}).status_code == 304
    )
    assert c.get(url + f"/files/{fids[0]}/thumbnail", headers=recipient).status_code == 200
    assert c.get(f"/v1/files/{fids[0]}/content", headers=recipient).status_code == 404
    refreshed = c.patch(url, headers=AUTH, json={"refresh": True})
    assert refreshed.status_code == 200 and refreshed.json()["through_run_id"] == later["id"]
    assert len(c.get(url + "/view", headers=recipient).json()["turns"]) == 2
    assert c.get(url + f"/files/{fids[2]}/content", headers=recipient).status_code == 200
    with SessionLocal() as db:
        db.get(Conversation, cid).deleted_at = utcnow()
        db.commit()
    assert c.get(url + "/view", headers=recipient).status_code == 404


def test_owner_from_principal_file_share_lifecycle_and_notification_seam(shared, monkeypatch):
    c, _, recipient, stranger, _, _, _owner, fids = shared
    recipient_id = c.get("/v1/me", headers=recipient).json()["id"]
    body = {"kind": "file", "file_id": fids[0], "audience": "people", "user_ids": [recipient_id]}
    assert c.post("/v1/shares", headers=AUTH, json={**body, "user_id": recipient_id}).status_code == 422
    assert c.post("/v1/shares", headers=stranger, json=body).status_code == 404
    notices = []
    monkeypatch.setattr("ailab_platform.api.routes_shares.create_notification", lambda *a, **kw: notices.append((a, kw)))
    created = c.post("/v1/shares", headers=AUTH, json=body)
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    assert len(notices) == 1 and notices[0][1]["share_id"] == sid
    assert c.patch(f"/v1/shares/{sid}", headers=AUTH, json={"user_ids": [recipient_id]}).status_code == 200
    assert len(notices) == 1
    data = c.get(f"/v1/shares/{sid}/view", headers=recipient).json()
    assert data["turns"] == [] and data["file"]["file_id"] == fids[0]
    with SessionLocal() as db:
        db.get(FileObject, fids[0]).deleted_at = utcnow()
        db.commit()
    assert c.get(f"/v1/shares/{sid}/view", headers=recipient).status_code == 404
    assert c.get(f"/v1/shares/{sid}/files/{fids[0]}/content", headers=AUTH).status_code == 404
    # Emptying trash must still work with an existing share FK (PG enforces this).
    from ailab_platform.library import purge_lineages

    with SessionLocal() as db:
        f = db.get(FileObject, fids[0])
        purge_lineages(db, f.user_id, [f.lineage_id])
        db.commit()
    assert c.get(f"/v1/shares/{sid}/view", headers=recipient).status_code == 404


def test_share_pagination_lookup_and_view_pages(shared):
    c, share, recipient, _, cid, first, owner, fids = shared
    assert any(r["id"] == share["id"] for r in c.get("/v1/shares?scope=received", headers=recipient).json()["items"])
    assert any(r["id"] == share["id"] for r in c.get(f"/v1/shares/for?conversation_id={cid}", headers=AUTH).json())
    assert c.get("/v1/users/lookup?q=", headers=AUTH).status_code == 422
    me = c.get("/v1/me", headers=recipient).json()
    results = c.get("/v1/users/lookup", params={"q": me["username"][:3]}, headers=AUTH).json()
    assert me["id"] in [u["id"] for u in results] and len(results) <= 8
    with SessionLocal() as db:
        parent = first
        for n in range(22):
            r = Run(user_id=owner, conversation_id=cid, parent_run_id=parent, prompt=f"p{n}", summary=f"a{n}", status="succeeded")
            db.add(r)
            db.flush()
            parent = r.id
        db.get(Conversation, cid).active_run_id = parent
        db.commit()
    c.patch(f"/v1/shares/{share['id']}", headers=AUTH, json={"refresh": True}).raise_for_status()
    one = c.get(f"/v1/shares/{share['id']}/view", headers=recipient).json()
    assert len(one["turns"]) == 20 and one["next_cursor"]
    two = c.get(f"/v1/shares/{share['id']}/view", params={"cursor": one["next_cursor"]}, headers=recipient).json()
    assert len(two["turns"]) == 3 and two["next_cursor"] is None
    for fid in fids[:2]:
        c.post("/v1/shares", headers=AUTH, json={"kind": "file", "file_id": fid, "audience": "everyone"}).raise_for_status()
    one = c.get("/v1/shares?limit=1", headers=AUTH).json()
    two = c.get("/v1/shares", params={"limit": 1, "cursor": one["next_cursor"]}, headers=AUTH).json()
    assert one["items"][0]["id"] != two["items"][0]["id"]
    object_page = c.get("/v1/shares/for", params={"conversation_id": cid, "limit": 1}, headers=AUTH)
    assert [r["id"] for r in object_page.json()] == [share["id"]]
    assert not object_page.headers.get("x-next-cursor")
    with SessionLocal() as db:
        db.get(User, me["id"]).disabled_at = utcnow()
        db.commit()
    try:
        assert c.get("/v1/users/lookup", params={"q": me["username"]}, headers=AUTH).json() == []
    finally:
        with SessionLocal() as db:
            db.get(User, me["id"]).disabled_at = None
            db.commit()


def test_share_query_indexes(shared):
    with SessionLocal() as db:
        statements = [
            (
                select(Share).where(Share.owner_id == "u", *live_conditions()).order_by(Share.created_at, Share.id).limit(20),
                "ix_shares_owner",
            ),
            (
                select(Share).where(Share.conversation_id == "c", Share.owner_id == "u", *live_conditions()).limit(50),
                "ix_shares_conversation",
            ),
            (select(Share).where(Share.file_id == "f", Share.owner_id == "u", *live_conditions()).limit(50), "ix_shares_file"),
            (select(Share).where(received("u"), *live_conditions()).limit(20), "share_recipients"),
        ]
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SET LOCAL enable_seqscan=off"))
        for stmt, index in statements:
            sql = str(stmt.compile(db.bind, compile_kwargs={"literal_binds": True}))
            prefix = "EXPLAIN " if db.bind.dialect.name == "postgresql" else "EXPLAIN QUERY PLAN "
            plan = " ".join(str(tuple(r)) for r in db.execute(text(prefix + sql)))
            assert index in plan, plan


def test_large_snapshot_view_query_budget_and_lookup_indexes(shared):
    import time

    from sqlalchemy import and_, event, or_

    from ailab_platform.db import engine

    c, share, recipient, _, cid, first, owner, _ = shared
    with SessionLocal() as db:
        parent = first
        for n in range(499):
            run = Run(user_id=owner, conversation_id=cid, parent_run_id=parent, prompt=f"p{n}", summary=f"a{n}", status="succeeded")
            db.add(run)
            db.flush()
            parent = run.id
        db.get(Conversation, cid).active_run_id = parent
        db.commit()
    c.patch(f"/v1/shares/{share['id']}", headers=AUTH, json={"refresh": True}).raise_for_status()
    statements = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        started = time.perf_counter()
        response = c.get(f"/v1/shares/{share['id']}/view", headers=recipient)
        elapsed = (time.perf_counter() - started) * 1000
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert response.status_code == 200 and len(response.json()["turns"]) == 20
    assert len(statements) <= 16, statements
    print(f"share-view-500: queries={len(statements)} ms={elapsed:.2f}")
    with SessionLocal() as db:
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SET LOCAL enable_seqscan=off"))
        queries = [
            (
                select(User.id)
                .where(
                    User.disabled_at.is_(None),
                    or_(and_(User.username >= "ab", User.username < "ac"), and_(User.display_name >= "ab", User.display_name < "ac")),
                )
                .limit(8),
                "ix_users_",
            ),
            (select(Artifact.id).where(Artifact.run_id.in_([first, parent]), Artifact.file_id == "f").limit(1), "ix_artifacts_run_id"),
        ]
        for query, index in queries:
            sql = str(query.compile(db.bind, compile_kwargs={"literal_binds": True}))
            prefix = "EXPLAIN " if db.bind.dialect.name == "postgresql" else "EXPLAIN QUERY PLAN "
            plan = " ".join(str(tuple(r)) for r in db.execute(text(prefix + sql)))
            assert index in plan, plan


def test_sharing_requires_terminal_leaf_and_file_share_is_fixed_version(shared):
    c, share, recipient, _, cid, first, owner, fids = shared
    with SessionLocal() as db:
        db.get(Run, first).status = "running"
        db.commit()
    payload = {"kind": "conversation", "conversation_id": cid, "audience": "everyone"}
    assert c.post("/v1/shares", headers=AUTH, json=payload).status_code == 409
    assert c.patch(f"/v1/shares/{share['id']}", headers=AUTH, json={"refresh": True}).status_code == 409
    with SessionLocal() as db:
        db.get(Run, first).status = "succeeded"
        original = db.get(FileObject, fids[0])
        original.is_head = 0
        db.flush()
        next_version = FileObject(
            user_id=owner,
            filename=original.filename,
            lineage_id=original.lineage_id,
            version=2,
            parent_id=original.id,
            is_head=1,
            storage_key="another-version",
            status="ready",
        )
        db.add(next_version)
        db.commit()
        newer = next_version.id
    # Sharing the retained version remains legal, and never grants the new version.
    created = c.post("/v1/shares", headers=AUTH, json={"kind": "file", "file_id": fids[0], "audience": "everyone"})
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    assert c.get(f"/v1/shares/{sid}/files/{fids[0]}/content", headers=recipient).content == b"public"
    assert c.get(f"/v1/shares/{sid}/files/{newer}/content", headers=recipient).status_code == 404


def test_share_locks_are_acquired_before_writes_in_global_order(shared, monkeypatch):
    from contextlib import contextmanager

    from ailab_platform.api import routes_shares

    c, _, recipient, _, cid, _, owner, _ = shared
    target = c.get("/v1/me", headers=recipient).json()["id"]
    taken = []
    original = routes_shares.user_critical_section

    @contextmanager
    def tracked(db, uid):
        taken.append(uid)
        with original(db, uid):
            yield

    monkeypatch.setattr(routes_shares, "user_critical_section", tracked)
    response = c.post(
        "/v1/shares", headers=AUTH, json={"kind": "conversation", "conversation_id": cid, "audience": "people", "user_ids": [target]}
    )
    assert response.status_code == 201, response.text
    assert taken == sorted([owner, target])
    taken.clear()
    response = c.patch("/v1/shares/" + response.json()["id"], headers=AUTH, json={"user_ids": [target]})
    assert response.status_code == 200, response.text
    assert taken == sorted([owner, target])


def test_reciprocal_shares_postgres_processes(shared):
    import json
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    from ailab_platform.db import engine

    if engine.dialect.name != "postgresql":
        pytest.skip("requires throwaway PostgreSQL")
    c, _, recipient, _, cid, _, owner, _ = shared
    target = c.get("/v1/me", headers=recipient).json()["id"]
    conv = c.post("/v1/conversations", headers=recipient, json={"title": "reciprocal"}).json()["id"]
    run = c.post(f"/v1/conversations/{conv}/messages", headers=recipient, json={"prompt": "reverse"}).json()["id"]
    _finish(c, run, "reverse")
    script = """
import json, sys, time
from sqlalchemy import text
from ailab_platform.db import SessionLocal
from ailab_platform.models import User
from ailab_platform.api.routes_shares import create, CreateShare
owner, target, conv, start = json.loads(sys.argv[1])
with SessionLocal() as db:
    db.execute(text("SET statement_timeout='5000ms'"))
    user = db.get(User, owner)
    time.sleep(max(0, start-time.time()))
    for _ in range(10):
        create(CreateShare(kind='conversation', conversation_id=conv, audience='people', user_ids=[target]), user, db)
print('ok')
"""
    start = time.time() + 1
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, json.dumps([u, v, cv, start])],
            env=os.environ.copy(),
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for u, v, cv in [(owner, target, cid), (target, owner, conv)]
    ]
    try:
        for proc in procs:
            out, err = proc.communicate(timeout=20)
            assert proc.returncode == 0, err
            assert out.strip() == "ok"
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_post_share_reuses_live_object_share(shared):
    c, share, _, _, cid, _, _, _ = shared
    repeated = c.post("/v1/shares", headers=AUTH, json={"kind": "conversation", "conversation_id": cid, "audience": "everyone"})
    assert repeated.status_code == 201
    assert repeated.json()["id"] == share["id"]
    assert repeated.json()["audience"] == "everyone"
