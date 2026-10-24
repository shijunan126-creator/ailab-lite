"""Preserve known ledger snapshots and leave unrecorded historical departments unknown."""

from sqlalchemy import inspect, text

from tests.test_migrations import _downgrade_to, _seed_user_and_run, _upgrade_to
from tests.test_migrations import engine as engine


def test_department_snapshot_roundtrip_does_not_guess_history(engine):
    _upgrade_to(engine, "0028")
    with engine.begin() as conn:
        _seed_user_and_run(conn)
        conn.execute(text("INSERT INTO departments(id,name,created_at) VALUES ('now','Current','2026-01-01')"))
        conn.execute(text("UPDATE users SET dept_id='now' WHERE id='u1'"))
        for call_id in ("open", "settled"):
            conn.execute(
                text(
                    "INSERT INTO usage_reservations(call_id,user_id,attempt_id,mode,alias,max_output_tokens,estimated_input_tokens,"
                    "weight,cached_token_weight,policy_version,reserved_amount,status,created_at,expires_at) "
                    "VALUES (:id,'u1','attempt','work','m',10,10,1,0.1,'test',20000,'open','2026-01-01','2026-01-02')"
                ),
                {"id": call_id},
            )
        conn.execute(
            text(
                "INSERT INTO model_usage(call_id,model,user_id,dept_id,created_at,prompt_tokens,completion_tokens) "
                "VALUES ('settled','m','u1','historical','2026-01-01',10,2)"
            )
        )
    _upgrade_to(engine, "0029")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT call_id,dept_id FROM usage_reservations ORDER BY call_id")).all() == [
            ("open", None),
            ("settled", "historical"),
        ]
    _downgrade_to(engine, "0028")
    assert "dept_id" not in {column["name"] for column in inspect(engine).get_columns("usage_reservations")}
    _upgrade_to(engine, "0029")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT dept_id FROM usage_reservations WHERE call_id='settled'")).scalar() == "historical"
