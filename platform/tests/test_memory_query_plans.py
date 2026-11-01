"""Memory queries on a populated throwaway database; never disable the real optimizer."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import event, insert, inspect, select, text
from sqlalchemy.orm import Session

from ailab_platform.memory_dream import service
from ailab_platform.memory_recall.search import _rows
from ailab_platform.migrate import run_migrations
from ailab_platform.models import MemoryDreamRun, MemoryWorkItem, Run, User
from tests.test_migrations import engine as engine


def test_memory_pending_and_history_queries_use_bounded_indexed_paths(engine):
    run_migrations(engine)
    with Session(engine) as db:
        users = [User(email=f"memory-query-plan-{i}@example.test") for i in range(4)]
        user = users[0]
        db.add_all(users)
        db.flush()
        uid = user.id
        now = datetime.now(UTC)
        db.execute(
            insert(Run),
            [
                {
                    "id": f"run_plan_{i}",
                    "user_id": users[i % 4].id,
                    "prompt": "查询计划语料",
                    "summary": "有索引的记忆读取",
                    "status": "succeeded",
                    "created_at": now - timedelta(seconds=i),
                }
                for i in range(40000)
            ],
        )
        db.execute(
            insert(MemoryWorkItem),
            [
                {"run_id": f"run_plan_{i}", "user_id": users[i % 4].id, "state": "done", "candidates": [], "updated_at": now}
                for i in range(400, 40000)
            ],
        )
        db.commit()
        db.execute(text("ANALYZE"))
        statements = []

        def capture(conn, cursor, sql, params, context, many):
            if sql.lstrip().upper().startswith(("SELECT", "WITH RECURSIVE")) and "runs" in sql:
                statements.append((sql, params))

        event.listen(engine, "before_cursor_execute", capture)
        try:
            assert len(_rows(db, uid, None, None)) == 400
            assert len(service._new_runs(db, uid, None, now)) == 40
            assert len(service._new_runs(db, uid, None, now, limit=201)) == 100
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert len(statements) == 3
        indexes = {i["name"] for i in inspect(engine).get_indexes("runs")}
        assert {"ix_runs_memory_history", "ix_runs_memory_index"} <= indexes
        for sql, params in statements:
            prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
            plan = "\n".join(str(row) for row in db.connection().exec_driver_sql(prefix + sql, params))
            # Existing user/time indexes with incremental sort are also valid optimizer choices.
            assert any(
                name in plan for name in ("ix_runs_memory_history", "ix_runs_memory_index", "ix_runs_user_created", "ix_runs_user_id")
            ), plan
            if "memory_work_items" in sql:
                assert "memory_work_items" in plan, plan


def test_dream_budget_and_active_queries_have_indexed_paths(engine):
    run_migrations(engine)
    with Session(engine) as db:
        users = [User(email=f"dream-plan-{i}@example.test") for i in range(64)]
        db.add_all(users)
        db.flush()
        now = datetime.now(UTC)
        db.execute(
            insert(MemoryDreamRun),
            [
                {
                    "id": f"dream_plan_{i}",
                    "user_id": users[i % 64].id,
                    "local_date": "2026-11-01",
                    "timezone": "UTC",
                    "status": "applied",
                    "claimed_until": now + timedelta(minutes=10) if i == 0 else None,
                }
                for i in range(8000)
            ],
        )
        db.commit()
        db.execute(text("ANALYZE"))
        queries = [
            select(MemoryDreamRun.id)
            .where(MemoryDreamRun.user_id == "user", MemoryDreamRun.local_date == "2026-10-30", MemoryDreamRun.trigger == "cron")
            .limit(6),
            select(MemoryDreamRun.id).where(MemoryDreamRun.user_id == users[0].id, service.active_job(now)).limit(1),
        ]
        indexes = {i["name"] for i in inspect(engine).get_indexes("memory_dream_runs")}
        assert {"uq_memory_dream_user_active", "ix_memory_dream_user_date_trigger"} <= indexes
        for query in queries:
            sql = str(query.compile(engine, compile_kwargs={"literal_binds": True}))
            prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
            plan = "\n".join(str(row) for row in db.connection().exec_driver_sql(prefix + sql))
            assert "INDEX" in plan.upper() or "BITMAP" in plan.upper(), plan


def test_summary_and_project_pool_queries_are_indexed(engine):
    from ailab_platform.models import MemorySummary, Project

    run_migrations(engine)
    with Session(engine) as db:
        users = [User(email=f"summary-plans-{i}@example.test") for i in range(32)]
        user = users[0]
        db.add_all(users)
        db.flush()
        db.execute(
            insert(Project),
            [
                {
                    "id": f"prj_plan_{i}",
                    "user_id": users[(i // 2) % 32].id,
                    "name": str(i),
                    "memory_mode": "isolated" if i % 2 else "shared",
                }
                for i in range(4000)
            ],
        )
        db.execute(
            insert(MemorySummary),
            [
                {"id": f"msum_plan_{i}", "user_id": user.id, "project_id": f"prj_plan_{i}", "text": "", "source_digest": ""}
                for i in range(4000)
            ],
        )
        db.add(MemorySummary(user_id=user.id, text="", source_digest=""))
        db.commit()
        db.execute(text("ANALYZE"))
        queries = [
            select(Project.id).where(Project.user_id == user.id, Project.memory_mode == "shared"),
            select(MemorySummary).where(MemorySummary.user_id == user.id, MemorySummary.project_id.is_(None)),
            select(MemorySummary).where(MemorySummary.user_id == user.id, MemorySummary.project_id == "prj_plan_8"),
            select(MemorySummary.id).where(MemorySummary.project_id == "prj_plan_8"),
            select(Project.id, MemorySummary.source_digest)
            .outerjoin(MemorySummary, (MemorySummary.project_id == Project.id) & (MemorySummary.user_id == user.id))
            .where(Project.user_id == user.id)
            .order_by(Project.updated_at.desc(), Project.id),
        ]
        for query in queries:
            sql = str(query.compile(engine, compile_kwargs={"literal_binds": True}))
            prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
            plan = "\n".join(str(row) for row in db.connection().exec_driver_sql(prefix + sql))
            assert "INDEX" in plan.upper() or "BITMAP" in plan.upper(), plan
