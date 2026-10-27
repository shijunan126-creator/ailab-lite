"""Memory queries on a populated throwaway database; never disable the real optimizer."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import event, insert, inspect, text
from sqlalchemy.orm import Session

from ailab_platform.memory_dream import service
from ailab_platform.memory_recall.search import _rows
from ailab_platform.migrate import run_migrations
from ailab_platform.models import MemoryWorkItem, Run, User
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
            if sql.lstrip().upper().startswith("SELECT") and "runs" in sql:
                statements.append((sql, params))

        event.listen(engine, "before_cursor_execute", capture)
        try:
            assert len(_rows(db, uid, None, None)) == 400
            assert len(service._new_runs(db, uid, None, now)) == 40
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert len(statements) == 2
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
