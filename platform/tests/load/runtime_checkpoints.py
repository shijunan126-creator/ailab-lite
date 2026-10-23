"""Opt-in checkpoint cost probe against an isolated API, without models or sandboxes.

Run in the 2 CPU verification VM with the API's container memory limit. The test
bootstrap creates a temporary database and never reads a developer environment.
"""

import argparse
import base64
import json
import random
import resource
import statistics
import sys
import time
from pathlib import Path

from tests import conftest  # noqa: F401 -- isolate before importing settings

# isort: split
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.api.main import app
from ailab_platform.api.reaper import drain_object_deletions
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import RunCheckpoint
from ailab_platform.storage import get_storage


def measure(steps, kib):
    state = fresh_state()
    state["messages"] = [{"role": "user", "content": base64.b64encode(random.Random(42).randbytes(kib * 768)).decode()}]
    durations = []
    with TestClient(app) as api:
        run = api.post("/v1/runs", headers={"Authorization": "Bearer test-token"}, json={"prompt": "checkpoint probe"}).json()["id"]
        headers = {"X-Service-Token": "test-service"}
        claim = api.post(f"/internal/jobs/{run}/claim", headers=headers, json={"consumer": "cost-probe"}).json()
        headers["X-Run-Attempt"] = claim["token"]
        api.post(f"/internal/runs/{run}/transition", headers=headers, json={"status": "running"}).raise_for_status()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu_start = usage.ru_utime + usage.ru_stime
        started = time.perf_counter()
        for step in range(steps):
            before = time.perf_counter()
            response = api.put(f"/internal/runs/{run}/checkpoint", headers=headers, json={"expected_step": step, "state": state})
            assert response.status_code == 200, response.text
            durations.append(time.perf_counter() - before)
        wall = time.perf_counter() - started
        usage = resource.getrusage(resource.RUSAGE_SELF)
        root = Path(settings.storage_root)
        disk_before = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(RunCheckpoint).where(RunCheckpoint.run_id == run)) == min(steps, 2)
            drain_object_deletions(db, get_storage())
        disk_after = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        return {
            "steps": steps,
            "state_bytes": len(json.dumps(state).encode()),
            "p95_seconds": statistics.quantiles(durations, n=100)[94],
            "wall_seconds": wall,
            "cpu_seconds": usage.ru_utime + usage.ru_stime - cpu_start,
            "peak_rss_mib": usage.ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024),
            "object_bytes_before_gc": disk_before,
            "object_bytes_after_gc": disk_after,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--kib", type=int, default=128)
    args = parser.parse_args()
    if args.steps < 2 or not 1 <= args.kib <= 3000:
        parser.error("steps >= 2 and 1 <= kib <= 3000 required")
    print(json.dumps(measure(args.steps, args.kib), indent=2))
