"""Reproduce the T14 history/workspace SQL on synthetic isolated databases (never imports app settings)."""

import json
import os
import resource
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text


def measure(url):
    engine = create_engine(url)
    postgres = engine.dialect.name == "postgresql"
    started = time.perf_counter()
    cpu = time.process_time()
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE runs (id varchar(40) PRIMARY KEY, conversation_id varchar(40), created_at timestamp, "
                "prompt text, summary text, error text, status varchar(30), result json)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX ix_runs_conversation_created_id ON runs (conversation_id, created_at, id)"
            )
        )
        for page in range(100):
            rows = [
                {
                    "id": f"r{n:06}",
                    "conversation_id": f"c{n // 1000:03}",
                    "created_at": datetime(2026, 1, 1) + timedelta(seconds=n),
                    "prompt": "问题" * 100,
                    "summary": "回答" * 100,
                    "result": json.dumps({"workspace_saved": n % 10 == 0}),
                }
                for n in range(page * 1000, (page + 1) * 1000)
            ]
            conn.execute(
                text(
                    "INSERT INTO runs(id,conversation_id,created_at,prompt,summary,result,status) "
                    "VALUES (:id,:conversation_id,:created_at,:prompt,:summary,:result,'succeeded')"
                ),
                rows,
            )
        conn.execute(text("ANALYZE"))
        where = "conversation_id='c042' AND (created_at,id) < ('2026-01-01 11:56:40','r043000')"
        history = (
            "SELECT id,substr(prompt,1,16000),substr(summary,1,16000),status,substr(error,1,16000) FROM runs WHERE "
            + where
            + " AND (created_at,id) > ('2026-01-01 11:48:20','r042500') ORDER BY created_at,id LIMIT 101"
        )
        saved = (
            "(result->>'workspace_saved')::boolean IS TRUE"
            if postgres
            else "json_extract(result,'$.workspace_saved') IS 1"
        )
        workspace = (
            "SELECT * FROM runs WHERE "
            + where
            + " AND "
            + saved
            + " ORDER BY created_at DESC,id DESC LIMIT 1"
        )
        output = {}
        for name, query in (("history", history), ("workspace", workspace)):
            prefix = (
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) "
                if postgres
                else "EXPLAIN QUERY PLAN "
            )
            plan = [list(r) for r in conn.execute(text(prefix + query))]
            elapsed = []
            for _ in range(20):
                tick = time.perf_counter()
                result = list(conn.execute(text(query)))
                elapsed.append((time.perf_counter() - tick) * 1000)
            output[name] = {
                "rows": len(result),
                "p95_ms": sorted(elapsed)[18],
                "plan": plan,
            }
    engine.dispose()
    return {
        "queries": output,
        "wall_seconds": time.perf_counter() - started,
        "cpu_seconds": time.process_time() - cpu,
        "process_peak_rss_bytes_macos": resource.getrusage(
            resource.RUSAGE_SELF
        ).ru_maxrss,
    }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="t14-query-plan-") as directory:
        result = {"sqlite": measure("sqlite:///" + directory + "/probe.db")}
    if os.environ.get("T14_PLAN_DATABASE_URL"):
        result["postgresql"] = measure(os.environ["T14_PLAN_DATABASE_URL"])
    Path(__file__).with_name("query-plans.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
