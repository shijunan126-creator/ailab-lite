"""Summary columns upgrade/downgrade without rebuilding referenced conversations."""

from sqlalchemy import inspect, text

from tests.test_migrations import _downgrade_to, _seed_user_and_run, _upgrade_to
from tests.test_migrations import engine as engine


def test_summary_columns_preserve_referenced_conversation(engine):
    _upgrade_to(engine, "0013")
    with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            conn.execute(text("PRAGMA foreign_keys=ON"))
        _seed_user_and_run(conn)
        conn.execute(
            text(
                "INSERT INTO conversations(id,user_id,title,model,created_at,updated_at) "
                "VALUES ('c1','u1','title','m','2026-01-01','2026-01-01')"
            )
        )
        conn.execute(text("UPDATE runs SET conversation_id='c1' WHERE id='r1'"))
    _upgrade_to(engine, "0016")
    with engine.begin() as conn:
        conn.execute(text("UPDATE conversations SET summary='remember',summary_through_run_id='r1' WHERE id='c1'"))
        assert conn.execute(text("SELECT conversation_id FROM runs WHERE id='r1'")).scalar() == "c1"
    _downgrade_to(engine, "0013")
    assert "summary" not in {c["name"] for c in inspect(engine).get_columns("conversations")}
    _upgrade_to(engine, "0016")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT title FROM conversations WHERE id='c1'")).scalar() == "title"
        assert conn.execute(text("SELECT summary FROM conversations WHERE id='c1'")).scalar() is None
        assert conn.execute(text("SELECT conversation_id FROM runs WHERE id='r1'")).scalar() == "c1"
