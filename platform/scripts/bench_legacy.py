"""Disposable same-process legacy parse benchmark; never a product execution path."""

import json
import resource
import sys
import time
from pathlib import Path

from ailab_platform.worker.parsers import extract_path

source = Path(sys.argv[1])
rows = []
for _ in range(2):
    started, cpu = time.perf_counter(), time.process_time()
    result = extract_path(source, source.name, "")
    rows.append(
        {
            "wall_ms": (time.perf_counter() - started) * 1000,
            "cpu_seconds": time.process_time() - cpu,
            "chars": result.get("chars"),
            "truncated": result.get("truncated"),
        }
    )
print(
    json.dumps(
        {"results": rows, "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)}
    )
)
