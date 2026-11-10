"""Project modes: the origin-project tag survives every write, mode switches move by it, isolation covers files (ADR 0036)."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from fastapi.testclient import TestClient

from ailab_platform.api.main import app
from ailab_platform.api.routes_memory import memory_for
from ailab_platform.contracts.memory import MemoryEntryInput
from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream.maintenance import save
from ailab_platform.memory_recall.invalidations import for_handles
from ailab_platform.memory_recall.store import replace_entries
from ailab_platform.models import Conversation, FileObject, MemoryItem, MessageFileRef, Run, User
from tests.test_library_agent_tools import SERVICE
from tests.test_memory_recall import _running, _seed, _uid
from tests.test_memory_summary import edit, project, scripted
from tests.test_memory_system_review import tool
from tests.test_skill_settings import _user


def _in_project(rid, uid, pid):
    with SessionLocal() as db:
        conv = Conversation(user_id=uid, project_id=pid, title="p")
        db.add(conv)
        db.flush()
        db.get(Run, rid).conversation_id = conv.id
        db.commit()
        return conv.id


def _tag(item_id):
    with SessionLocal() as db:
        return db.get(MemoryItem, item_id).origin_project_id


def test_origin_project_survives_every_write_path(monkeypatch):
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        pid = project(c, me)
        rid = _running(c, me)
        _in_project(rid, uid, pid)
        added = tool(c, me, rid, "a", action="add", kind="fact", content="在做券商研报自动化", explicit=True)
        assert added.status_code == 200, added.text
        line = added.json()["entries"][0]
        assert line["project_id"] is None and _tag(line["id"]) == pid  # shared project: account scope, project tag

        # an older Portal client that does not know the field keeps it
        current = c.get("/v1/memory", headers=me).json()
        fields = set(MemoryEntryInput.model_fields) - {"origin_project_id"}
        body = [{k: v for k, v in e.items() if k in fields} for e in current["entries"]]
        other = {"kind": "preference", "content": "回答先给结论"}
        saved = c.put("/v1/memory", headers=me, json={"expected_revision": current["revision"], "entries": [*body, other]})
        assert saved.status_code == 200, saved.text
        assert _tag(line["id"]) == pid

        # the nightly save path (inputs -> write_memory) keeps it
        with SessionLocal() as db:
            owner = db.get(User, uid)
            before = memory_for(owner)
            entries = [dict(e) for e in before["entries"]]
            entries.append({"id": "mem_n" + uid[4:20], "kind": "interest", "content": "关注量化交易", "origin": "inferred"})
            save(db, owner, before, entries)
            db.commit()
        assert _tag(line["id"]) == pid

        # a correction from the tool inherits the tag; a page edit keeps it
        replaced = tool(c, me, rid, "b", action="replace", id=line["id"], content="在做券商研报自动化，已接入三家数据源")
        assert replaced.status_code == 200, replaced.text
        newer = replaced.json()["entries"][0]["id"]
        assert _tag(newer) == pid
        scripted(monkeypatch, ops=[{"op": "replace", "id": newer, "content": "在做研报自动化，接入三家数据源"}])
        edit(c, me, "改一下这条")
        assert _tag(newer) == pid
        assert c.get("/v1/memory", headers=me).json()["entries"][-1]["origin_project_id"] in {pid, None}


def test_switching_modes_moves_lines_by_tag_and_back():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        pid = project(c, me)
        tagged, review, untagged = ("mem_t" + uid[4:20], "mem_r" + uid[4:20], "mem_u" + uid[4:20])
        with SessionLocal() as db:
            replace_entries(
                db,
                db.get(User, uid),
                [
                    {"id": tagged, "kind": "project", "content": "推进研报自动化", "origin_project_id": pid, "origin": "inferred"},
                    {"id": review, "kind": "fact", "content": "患有轻度近视", "origin_project_id": pid, "status": "review"},
                    {"id": untagged, "kind": "preference", "content": "回答先给结论", "origin": "explicit"},
                ],
            )
            db.commit()
        assert c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "isolated"}).status_code == 200
        with SessionLocal() as db:
            assert db.get(MemoryItem, tagged).project_id == pid and db.get(MemoryItem, review).project_id == pid
            assert db.get(MemoryItem, untagged).project_id is None
            assert db.query(MemoryItem).filter(MemoryItem.user_id == uid, MemoryItem.status != "closed").count() == 3  # moved, not copied
            # an account conversation that saw the line hears it left the scope, not that it was deleted
            resolved = for_handles(db, uid, [tagged], memory_for(db.get(User, uid)), None, "shared")[tagged]
            assert resolved == {"current": None, "change_type": None, "out_of_scope": True}
        assert c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "shared"}).status_code == 200
        with SessionLocal() as db:
            back = db.get(MemoryItem, tagged)
            assert back.project_id is None and back.origin_project_id == pid


def test_isolated_to_shared_merge_tags_moved_lines():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        pid = project(c, me, "isolated")
        own = "mem_i" + uid[4:20]
        with SessionLocal() as db:
            replace_entries(db, db.get(User, uid), [{"id": own, "kind": "fact", "content": "项目内事实", "project_id": pid}])
            db.commit()
        assert c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "shared"}).status_code == 200
        with SessionLocal() as db:
            row = db.get(MemoryItem, own)
            assert row.project_id is None and row.origin_project_id == pid


def test_deleting_a_project_clears_tags_and_keeps_lines():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        pid = project(c, me)
        mid = "mem_d" + uid[4:20]
        with SessionLocal() as db:
            replace_entries(db, db.get(User, uid), [{"id": mid, "kind": "fact", "content": "在券商工作", "origin_project_id": pid}])
            db.commit()
        assert c.delete(f"/v1/projects/{pid}", headers=me).status_code == 204
        with SessionLocal() as db:
            row = db.get(MemoryItem, mid)
            assert row.status == "active" and row.project_id is None and row.origin_project_id is None


def _file(db, uid, pid, name):
    file = FileObject(user_id=uid, filename=name, storage_key="unused", status="ready", purpose="library", project_id=pid)
    db.add(file)
    db.flush()
    return file


def test_isolated_project_files_stay_inside_unless_the_user_attaches_one():
    from ailab_platform.api.routes_library_tools import authorized_source

    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        isolated, shared = project(c, me, "isolated"), project(c, me)
        inside_run, account_run = _running(c, me), _running(c, me)
        _in_project(inside_run, uid, isolated)
        with SessionLocal() as db:
            secret = _file(db, uid, isolated, "isolated-plan.txt")
            outside = _file(db, uid, None, "account-note.txt")
            shared_file = _file(db, uid, shared, "shared-note.txt")
            db.commit()
            ids = {f.filename: f.id for f in (secret, outside, shared_file)}

        def read(rid, name):
            with SessionLocal() as db:
                try:
                    authorized_source(db, db.get(Run, rid), ids[name])
                    return True
                except HTTPException as exc:
                    assert exc.status_code == 404
                    return False

        assert read(inside_run, "isolated-plan.txt")
        assert not read(inside_run, "account-note.txt") and not read(inside_run, "shared-note.txt")
        assert not read(account_run, "isolated-plan.txt")
        assert read(account_run, "account-note.txt") and read(account_run, "shared-note.txt")

        # the user attaching an outside file in this conversation lets it in
        with SessionLocal() as db:
            db.add(
                MessageFileRef(
                    run_id=inside_run, message_kind="run_prompt", message_id=inside_run, file_id=ids["account-note.txt"], ordinal=0
                )
            )
            db.commit()
        assert read(inside_run, "account-note.txt")

        def found(rid):
            response = c.post(f"/internal/runs/{rid}/library/search", headers=SERVICE, json={"query": "-"})
            return {f["filename"] for f in response.json()["files"]}

        assert found(inside_run) == {"isolated-plan.txt"}
        assert found(account_run) == {"account-note.txt", "shared-note.txt"}


def test_shared_project_recall_reaches_older_project_turns(monkeypatch):
    from ailab_platform.memory_recall import search

    monkeypatch.setattr(search, "MAX_DOCS", 5)
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        pid = project(c, me)
        with SessionLocal() as db:
            conv = Conversation(user_id=uid, project_id=pid, title="研报")
            db.add(conv)
            db.commit()
            cid = conv.id
        old = _seed(uid, "研报自动化的数据源选型用万得", days_ago=30, conversation_id=cid)
        for i in range(8):
            _seed(uid, f"别的话题第{i}次", days_ago=1)
        with SessionLocal() as db:
            user = db.get(User, uid)
            hits = search.search_history(db, user, "研报 数据源 万得", project_id=pid, now=datetime.now(UTC) + timedelta(minutes=1))
            assert [r["run_id"] for r in hits["results"]] == [old]
            account = search.search_history(db, user, "研报 数据源 万得", now=datetime.now(UTC) + timedelta(minutes=1))
            assert account["results"] == []  # only the project adds the extra batch; pagination is unchanged
            assert account["scanned"] == hits["scanned"] == 5
