"""EXPLAIN runtime queries on synthetic rows in an isolated SQLite file or a fresh PostgreSQL schema."""

import argparse
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ["AILAB_ENV_FILE"] = "/no-such-runtime-probe.env"

# isort: split
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from ailab_platform.migrate import run_migrations
from ailab_platform.models import OutboxMessage, Run, RunCheckpoint, User


def measure(engine):
    run_migrations(engine)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(User.__table__.insert(), [{"id": "probe-user", "email": "probe@example.test", "display_name": "Probe"}])
        conn.execute(
            Run.__table__.insert(),
            [
                {
                    "id": f"probe-{i:05}",
                    "user_id": "probe-user",
                    "title": "probe",
                    "prompt": "probe",
                    "model": "mock",
                    "status": "waiting_user" if i < 50 else "succeeded" if i < 150 else "queued",
                    "waiting_since": now - timedelta(days=4) if i < 50 else None,
                    "finished_at": now - timedelta(days=8) if 50 <= i < 150 else None,
                }
                for i in range(10000)
            ],
        )
        conn.execute(
            OutboxMessage.__table__.insert(),
            [{"id": f"job-{i}", "topic": "probe", "payload": {}, "claim_token": f"token-{i}"} for i in range(10000)],
        )
        conn.execute(
            RunCheckpoint.__table__.insert(),
            [
                {
                    "run_id": f"probe-{i:05}",
                    "step": 1,
                    "attempt_id": "probe",
                    "state_key": f"checkpoints/probe-{i}",
                    "plan": [],
                    "tool_log": {},
                    "deliveries": {},
                    "expires_at": now - timedelta(days=1) if i < 150 else None,
                }
                for i in range(50, 1050)
            ],
        )
        conn.execute(text("ANALYZE"))
        queries = {
            "waiting": "SELECT id FROM runs WHERE status='waiting_user' AND waiting_since<'2026-10-23' ORDER BY waiting_since,id LIMIT 100",
            "claim": "SELECT id FROM outbox WHERE claim_token='token-5000'",
            "checkpoint_object": "SELECT run_id FROM run_checkpoints WHERE state_key='checkpoints/probe-100' LIMIT 1",
            "checkpoint_gc": "SELECT run_id,step FROM run_checkpoints WHERE expires_at<'2026-10-24' "
            "ORDER BY expires_at,run_id,step LIMIT 100",
        }
        prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
        result = {name: [list(row) for row in conn.execute(text(prefix + query))] for name, query in queries.items()}
        for name, index in (
            ("waiting", "ix_runs_waiting"),
            ("claim", "ix_outbox_claim_token"),
            ("checkpoint_object", "ix_run_checkpoints_state_key"),
            ("checkpoint_gc", "ix_run_checkpoints_expiry"),
        ):
            assert index in str(result[name]), result[name]
        return {"runs": 10000, "outbox": 10000, "checkpoints": 1000, "plans": result}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres-url", help="throwaway server only; creates and removes its own unique schema")
    args = parser.parse_args()
    if args.postgres_url:
        schema = "runtime_probe_" + uuid.uuid4().hex
        admin = create_engine(args.postgres_url, poolclass=NullPool)
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(args.postgres_url, connect_args={"options": f"-csearch_path={schema}"}, poolclass=NullPool)
        try:
            print(json.dumps(measure(engine), indent=2))
        finally:
            engine.dispose()
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()
    else:
        with tempfile.TemporaryDirectory(prefix="ailab-runtime-plans-") as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'plans.db'}")
            try:
                print(json.dumps(measure(engine), indent=2))
            finally:
                engine.dispose()
