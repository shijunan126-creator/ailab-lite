"""Hot memory queries and historical cleanup on SQLite and CI's throwaway PostgreSQL."""

from datetime import timedelta

from sqlalchemy import select, text

from ailab_platform.db import SessionLocal, init_db
from ailab_platform.memory_recall.retention import purge_closed
from ailab_platform.memory_recall.store import entries_for, replace_entries
from ailab_platform.models import MemoryDreamRun, MemoryItem, User, utcnow


def test_closed_history_is_indexed_and_never_loaded_by_writes():
    init_db()
    now = utcnow()
    with SessionLocal() as db:
        user = User(email=f"memory-index-{now.timestamp()}@test.invalid")
        db.add(user)
        db.flush()
        for i in range(2000):
            db.add(
                MemoryItem(user_id=user.id, content=f"history{i}", content_hash=str(i), status="closed", valid_to=now - timedelta(days=100))
            )
        peers = [User(email=f"memory-plan-peer-{now.timestamp()}-{i}@test.invalid") for i in range(20)]
        db.add_all(peers)
        db.flush()
        for peer in peers:
            db.add_all(MemoryItem(user_id=peer.id, content=f"peer{i}", content_hash=f"peer{i}", status="active") for i in range(50))
        db.add(MemoryItem(id="mem_hot", user_id=user.id, content="active", content_hash="active", status="active"))
        db.commit()
        db.execute(text("ANALYZE memory_items"))
        plan = " ".join(
            str(row)
            for row in db.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT id FROM memory_items WHERE user_id=:uid AND project_id IS NULL AND status='active'"
                    if db.get_bind().dialect.name == "sqlite"
                    else "EXPLAIN SELECT id FROM memory_items WHERE user_id=:uid AND project_id IS NULL AND status='active'"
                ),
                {"uid": user.id},
            )
        )
        assert any(index in plan for index in ("ix_memory_items_scope", "uq_memory_items_hash")), plan
        assert "Seq Scan" not in plan
        entries = entries_for(user)
        assert len(entries) == 1 and entries[0]["id"] == "mem_hot"
        entries[0]["content"] = "changed"
        replace_entries(db, user, entries)
        db.commit()
        assert len(entries_for(user)) == 1
        assert db.scalar(
            select(MemoryItem.content).where(MemoryItem.user_id == user.id, MemoryItem.status == "closed").limit(1)
        ).startswith("history")
        for peer in peers:
            db.delete(peer)
        db.delete(user)
        db.commit()


def test_retention_preserves_revision_valid_undo_then_collects():
    init_db()
    now = utcnow()
    with SessionLocal() as db:
        user = User(email=f"memory-retention-{now.timestamp()}@test.invalid", settings={"memory": {"revision": 1}})
        db.add(user)
        db.flush()
        old = MemoryItem(
            user_id=user.id, content="closed protected", content_hash="protected", status="closed", valid_to=now - timedelta(days=100)
        )
        recent = MemoryItem(
            user_id=user.id, content="recent closed", content_hash="recent", status="closed", valid_to=now - timedelta(days=1)
        )
        db.add_all([old, recent])
        db.flush()
        old_id, recent_id = old.id, recent.id
        job = MemoryDreamRun(
            user_id=user.id,
            status="applied",
            trigger="manual",
            local_date="2026-01-01",
            timezone="UTC",
            change_set={"revision": 1, "items": [{"id": old.id, "before": {"id": old.id}, "after": None}]},
        )
        db.add(job)
        db.commit()
        purge_closed(db, now)
        assert db.get(MemoryItem, old_id) and db.get(MemoryItem, recent_id)
        user.settings = {"memory": {"revision": 2}}
        db.commit()
        purge_closed(db, now)
        assert db.get(MemoryItem, old_id) is None and db.get(MemoryItem, recent_id)
        db.delete(user)
        db.commit()
