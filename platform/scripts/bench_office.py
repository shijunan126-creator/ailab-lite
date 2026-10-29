"""Equal small-fixture ranges; records raw measurements, never claims production capacity."""

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import office_benchmark_process as office

from ailab_platform.worker.isolate import execute

root = Path(__file__).resolve().parents[2]
fixtures = root / "vendor/zcode-office-runtime/fixtures"
rows = []
for kind, name in [("pdf", "paper.pdf"), ("docx", "report.docx"), ("xlsx", "budget.xlsx"), ("pptx", "launch.pptx")]:
    source = fixtures / name
    with tempfile.TemporaryDirectory(prefix="ailab-office-bench-") as tmp:
        tmp = Path(tmp)
        started = time.perf_counter()
        old = execute("extract", source, tmp / "old", filename=name, content_type="", measure=True)
        old_wall = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        new = office.execute(source, tmp / "new")
        new_wall = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        digest = hashlib.sha256()
        for unit in new["units"]:
            digest.update((tmp / "new" / unit["name"]).read_bytes())
        persistent_ms = (time.perf_counter() - started) * 1000
        hot = json.loads(
            subprocess.check_output(["node", str(root / "vendor/zcode-office-runtime/bench.mjs"), str(source), kind], text=True)
        )
        legacy_hot = json.loads(
            subprocess.check_output([sys.executable, str(root / "platform/scripts/bench_legacy.py"), str(source)], text=True)
        )
        rows.append(
            {
                "format": kind,
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "range": "all native paragraphs/tables"
                if kind == "docx"
                else "all sheets, rows and columns"
                if kind == "xlsx"
                else "all pages/slides",
                "legacy": {
                    "cold_wall_ms": old_wall,
                    "same_process": legacy_hot,
                    **old.get("measurement", {}),
                    "chars": old.get("chars"),
                    "truncated": old.get("truncated"),
                },
                "office": {
                    "cold_wall_ms": new_wall,
                    **new["measurement"],
                    "chars": new["chars"],
                    "units": new["total_units"],
                    "same_process": hot,
                    "persistent_object_read_ms": persistent_ms,
                    "derived_bytes": sum(p.stat().st_size for p in (tmp / "new").glob("*.txt")),
                },
                "coverage": (
                    "small upstream fixtures stay inside legacy 20-page/200-row/40-column limits; "
                    "locators are additional output, not byte-identical text"
                ),
            }
        )
out = root / "docs/reviews/2026-10-29-office-performance.json"
out.write_text(
    json.dumps(
        {
            "environment": "macOS ARM64 development host; Node " + subprocess.check_output(["node", "--version"], text=True).strip(),
            "scope": "local disposable processes; no target 2-vCPU capacity, model TTFT or container-layer measurement",
            "rows": rows,
        },
        indent=2,
    )
    + "\n"
)
print(out)
