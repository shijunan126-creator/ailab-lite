"""EXPLAIN the actual bounded repair/GC/read queries on selective fixtures."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import event
from sqlalchemy.orm import Session

from ailab_platform.api import process_snapshots as ps
from ailab_platform.api.reaper import purge_run_events
from ailab_platform.migrate import run_migrations
from ailab_platform.models import Run, RunEvent, User
from tests.test_migrations import engine as engine


def test_process_queries_use_indexed_run_and_event_access(engine, monkeypatch):
    run_migrations(engine)
    old = datetime.now(UTC) - timedelta(days=100)
    with engine.begin() as conn:
        conn.execute(User.__table__.insert().values(id="process-query-user", email="process-query@example.test", display_name="Q"))
        conn.execute(
            Run.__table__.insert(),
            [
                dict(
                    id=f"process-query-{i:05}",
                    user_id="process-query-user",
                    prompt="p",
                    status="failed" if i < 5 else "running",
                    finished_at=old,
                )
                for i in range(3000)
            ],
        )
        conn.execute(
            RunEvent.__table__.insert(),
            [
                dict(run_id=f"process-query-{i:05}", seq=j + 1, type="agent.thinking", payload={"step": 0, "text": "think"}, created_at=old)
                for i in range(5)
                for j in range(10)
            ],
        )
        conn.execute(
            RunEvent.__table__.insert(),
            [dict(run_id=f"process-query-{i:05}", seq=1, type="heartbeat", payload={}, created_at=old) for i in range(5, 3000)],
        )
        conn.exec_driver_sql("ANALYZE")
    statements = []

    def capture(conn, cursor, statement, params, context, many):
        if statement.lstrip().upper().startswith("SELECT") and ("run_events" in statement or "FROM runs" in statement):
            statements.append((statement, params))

    monkeypatch.setattr(ps, "_repair_cursor", "")
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with Session(engine) as db:
            ps.repair_snapshots(db)
            ps.derive(db, "process-query-00000")
            purge_run_events(db, datetime.now(UTC))
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    plans = []
    with engine.connect() as conn:
        for statement, params in statements:
            prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
            plans.append("\n".join(str(tuple(row)) for row in conn.exec_driver_sql(prefix + statement, params)))
    assert plans
    combined = "\n".join(plans).lower()
    assert "index" in combined
    event_plans = [p.lower() for (stmt, _), p in zip(statements, plans, strict=True) if "run_events" in stmt]
    assert all("index" in p and ("run_events" in p or "uq_run_event_seq" in p) for p in event_plans), combined
    assert not any("seq scan on run_events" in p or ("scan run_events" in p and "using" not in p) for p in event_plans), combined
