"""0037 is reversible without rebuilding referenced SQLite runs; lookup uses the unique index."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.test_migrations import _downgrade_to, _seed_user_and_run, _upgrade_to
from tests.test_migrations import engine as engine


def test_client_send_upgrade_lookup_uniqueness_and_downgrade(engine):
    _upgrade_to(engine, "0036")
    with engine.begin() as conn:
        _seed_user_and_run(conn)
    _upgrade_to(engine, "0037")
    with engine.begin() as conn:
        conn.execute(text("UPDATE runs SET client_send_id='key' WHERE id='r1'"))
        # Small fixtures may prefer a sequential scan; prove the exact production predicate is indexable.
        if engine.dialect.name == "postgresql":
            conn.execute(text("SET LOCAL enable_seqscan=off"))
        explain = "EXPLAIN QUERY PLAN" if engine.dialect.name == "sqlite" else "EXPLAIN"
        plan = str(
            conn.execute(
                text(explain + " SELECT id FROM runs WHERE user_id=:user AND client_send_id=:key"), {"user": "u1", "key": "key"}
            ).all()
        )
        assert "uq_runs_user_client_send" in plan
    with engine.connect() as conn:
        table = inspect(conn).get_columns("runs")
        columns = [c["name"] for c in table]
        names = ",".join(columns)
        source = ",".join("'r2'" if c == "id" else c for c in columns)
        with pytest.raises(IntegrityError):
            conn.execute(text(f"INSERT INTO runs ({names}) SELECT {source} FROM runs WHERE id='r1'"))
        conn.rollback()
    _downgrade_to(engine, "0036")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT prompt FROM runs WHERE id='r1'")).scalar() == "p"
        assert "client_send_id" not in {c["name"] for c in inspect(conn).get_columns("runs")}
    _upgrade_to(engine, "0037")


def test_queue_client_send_upgrade_lookup_uniqueness_and_downgrade(engine):
    _upgrade_to(engine, "0037")
    with engine.begin() as conn:
        _seed_user_and_run(conn)
        conn.execute(
            text(
                "INSERT INTO conversations (id,user_id,title,model,created_at,updated_at) "
                "VALUES ('c1','u1','t','m','2026-01-01','2026-01-01')"
            )
        )
        # Legacy duplicate options must not prevent upgrading.
        for qid in ("q1", "q2"):
            conn.execute(
                text(
                    "INSERT INTO queued_messages "
                    "(id,conversation_id,user_id,prompt,file_ids,model,options,steer,state,created_at,updated_at) "
                    "VALUES (:id,'c1','u1','p','[]','m',:options,false,'queued','2026-01-01','2026-01-01')"
                ),
                {"id": qid, "options": '{"client_send_id":"legacy"}'},
            )
    _upgrade_to(engine, "0038")
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT count(*) FROM queued_messages WHERE client_send_id IS NULL")) == 2
        conn.execute(text("UPDATE queued_messages SET client_send_id='key' WHERE id='q1'"))
        if engine.dialect.name == "postgresql":
            conn.execute(text("SET LOCAL enable_seqscan=off"))
        explain = "EXPLAIN QUERY PLAN" if engine.dialect.name == "sqlite" else "EXPLAIN"
        plan = str(conn.execute(text(explain + " SELECT id FROM queued_messages WHERE user_id='u1' AND client_send_id='key'")).all())
        assert "uq_queued_messages_user_client_send" in plan
    with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(text("UPDATE queued_messages SET client_send_id='key' WHERE id='q2'"))
        conn.rollback()
        # Ordinary reaper-style deletion does not depend on the new identity.
        conn.execute(text("DELETE FROM queued_messages WHERE id='q2'"))
        conn.commit()
    _downgrade_to(engine, "0037")
    with engine.connect() as conn:
        assert "client_send_id" not in {c["name"] for c in inspect(conn).get_columns("queued_messages")}
        assert conn.scalar(text("SELECT prompt FROM queued_messages WHERE id='q1'")) == "p"
        assert conn.scalar(text("SELECT count(*) FROM runs WHERE id='r1'")) == 1
    _upgrade_to(engine, "0038")
