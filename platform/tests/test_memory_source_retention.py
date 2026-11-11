"""Deleting a source removes the run and quote from a remembered line; the line itself stays (ADR 0036)."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.api.routes_memory import memory_for
from ailab_platform.db import SessionLocal
from ailab_platform.memory_recall.store import replace_entries
from ailab_platform.models import Conversation, MemoryItem, MemoryItemSource, User, utcnow
from tests.test_memory_recall import _conversation, _seed, _uid
from tests.test_memory_summary import project
from tests.test_skill_settings import _user


def _remember(uid, rid, *, closed=False, **extra):
    """One inferred nightly line learned only from `rid`, plus an optional closed version that only depended on it."""
    current, old = "mem_c" + uid[4:20], "mem_o" + uid[4:20]
    entries = [
        {
            "id": current,
            "kind": "fact",
            "origin": "inferred",
            "source": "nightly",
            "content": "用户在一家券商做量化研究",
            "source_quote": "我在券商做量化",
            "evidence_run_ids": [rid],
            **extra,
        }
    ]
    if closed:
        entries.append(
            {
                "id": old,
                "kind": "fact",
                "origin": "inferred",
                "source": "nightly",
                "content": "用户在券商实习",
                "source_quote": "我在券商实习",
                "evidence_run_ids": [rid],
                "invalid_at": utcnow().isoformat(),
            }
        )
    with SessionLocal() as db:
        replace_entries(db, db.get(User, uid), entries)
        db.commit()
    return current, old


def _view_item(c, me, item_id, pid=None):
    query = f"?project_id={pid}" if pid else ""
    view = c.get(f"/v1/memory/view{query}", headers=me).json()
    item = next(i for s in view["sections"] for i in s["items"] if i["id"] == item_id)
    assert "sources" not in item  # sources are kept for organizing and deletion, never shown
    return item


def _indexed_sources(db, item_id):
    return set(db.scalars(select(MemoryItemSource.run_id).where(MemoryItemSource.memory_id == item_id)))


def test_deleting_the_only_source_conversation_keeps_the_line_without_a_source():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        conv = _conversation(uid, "量化")
        rid = _seed(uid, "我在券商做量化", conversation_id=conv)
        current, old = _remember(uid, rid, closed=True)
        with SessionLocal() as db:
            assert db.get(MemoryItem, current).source_run_ids == [rid] and _indexed_sources(db, current) == {rid}
        assert c.delete(f"/v1/conversations/{conv}", headers=me).status_code == 204
        item = _view_item(c, me, current)
        assert item["content"] == "用户在一家券商做量化研究"
        with SessionLocal() as db:
            row = db.get(MemoryItem, current)
            assert row.status == "active" and row.source_run_ids == [] and row.source_quote == ""
            assert _indexed_sources(db, current) == set() and _indexed_sources(db, old) == set()
            assert db.get(MemoryItem, old) is None  # history that only lived on the deleted source still goes
            entry = next(e for e in memory_for(db.get(User, uid))["entries"] if e["id"] == current)
            assert entry["evidence_deleted"] is True


def test_deleting_a_project_keeps_account_lines_learned_there():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        pid = project(c, me)
        conv = c.post("/v1/conversations", headers=me, json={"project_id": pid}).json()["id"]
        rid = _seed(uid, "我在券商做量化", conversation_id=conv)
        current, _ = _remember(uid, rid)
        assert c.delete(f"/v1/projects/{pid}", headers=me).status_code == 204
        with SessionLocal() as db:
            assert db.get(Conversation, conv).deleted_at is not None
            row = db.get(MemoryItem, current)
            assert row.status == "active" and row.source_run_ids == [] and row.project_id is None
            assert _indexed_sources(db, current) == set()
        _view_item(c, me, current)


def test_deleting_a_line_drops_its_indexed_sources():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        rid = _seed(uid, "我在券商做量化", conversation_id=_conversation(uid, "量化"))
        current, _ = _remember(uid, rid)
        revision = c.get("/v1/memory/view", headers=me).json()["revision"]
        assert c.delete(f"/v1/memory/items/{current}?expected_revision={revision}", headers=me).status_code in (200, 204)
        with SessionLocal() as db:
            assert db.get(MemoryItem, current) is None and _indexed_sources(db, current) == set()


# File-source invalidation (online read, undo snapshot and plan copies) is covered by
# tests/test_document_context.py::test_file_deletion_fences_dream_raw_recall_and_snapshot_replay.
