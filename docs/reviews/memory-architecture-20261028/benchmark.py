"""Isolated memory benchmark; pass only a fresh disposable PostgreSQL database."""

import argparse
import json
import math
import os
import resource
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--database-url")
parser.add_argument("--output", required=True)
args = parser.parse_args()
data = Path(tempfile.mkdtemp(prefix="ailab-memory-benchmark-"))
os.environ.update(AILAB_ENV_FILE=str(data / "no-env"), AILAB_ENV="test", AILAB_DATA_DIR=str(data),
                  AILAB_DATABASE_URL=args.database_url or f"sqlite:///{data / 'bench.db'}",
                  AILAB_MODELS_FILE=str(Path(__file__).resolve().parents[3] / "platform/tests/models.test.toml"))

from sqlalchemy import event, insert, text  # noqa: E402

from ailab_platform.db import SessionLocal, engine, init_db  # noqa: E402
from ailab_platform.memory_dream.service import _new_runs  # noqa: E402
from ailab_platform.memory_recall.search import search_history  # noqa: E402
from ailab_platform.models import MemoryWorkItem, Run, User  # noqa: E402

init_db()
with SessionLocal() as db:
    db.add_all([User(id=f"usr_bench_{i}", email=f"bench{i}@example.test") for i in range(4)])
    db.commit()
    now = datetime.now(UTC)
    for start in range(0, 40000, 1000):
        db.execute(insert(Run), [{"id": f"run_bench_{i}", "user_id": f"usr_bench_{i % 4}", "status": "succeeded",
            "prompt": f"第{i}轮的 Kubernetes 沙箱销毁决策", "summary": "选择 Kubernetes 并在空闲后销毁沙箱。" * 30,
            "created_at": now - timedelta(seconds=i)} for i in range(start, start + 1000)])
        db.execute(insert(MemoryWorkItem), [{"run_id": f"run_bench_{i}", "user_id": f"usr_bench_{i % 4}",
            "state": "done", "candidates": [], "updated_at": now} for i in range(max(start, 400), start + 1000)])
        db.commit()
    db.execute(text("ANALYZE"))
    user = db.get(User, "usr_bench_0")
    captured = []

    def capture(conn, cursor, sql, params, ctx, many):
        if sql.startswith("SELECT") and "runs" in sql:
            captured.append((sql, params))

    event.listen(engine, "before_cursor_execute", capture)
    search_history(db, user, "Kubernetes 沙箱销毁")
    _new_runs(db, user.id, None, now)
    event.remove(engine, "before_cursor_execute", capture)
    plans = []
    for sql, params in captured:
        prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
        plans.append([str(row) for row in db.connection().exec_driver_sql(prefix + sql, params)])
    durations = []
    started_cpu = time.process_time()
    for _ in range(50):
        start = time.perf_counter()
        result = search_history(db, user, "Kubernetes 沙箱销毁")
        _new_runs(db, user.id, None, now)
        durations.append((time.perf_counter() - start) * 1000)
    out = {"backend": engine.dialect.name, "total_runs": 40000, "user_runs": 10000, "iterations": 50,
           "history_scanned": result["scanned"], "history_results": len(result["results"]),
           "combined_read_p95_ms": sorted(durations)[math.ceil(len(durations) * 0.95) - 1],
           "combined_read_max_ms": max(durations), "cpu_seconds": time.process_time() - started_cpu,
           "process_maxrss_platform_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
           "plans": plans, "limits": "Mac local synthetic data; no embedding/model call, no target-machine capacity claim"}
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "plans"}, ensure_ascii=False))
