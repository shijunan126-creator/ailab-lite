"""Compare bounded local listing with the pinned pre-index adapter on synthetic data."""

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import time
import types
from pathlib import Path

from ailab_platform.storage import LocalStorage

ROOT = Path(__file__).resolve().parents[4]


def measure(function, repeats=30):
    wall, cpu = [], []
    for _ in range(repeats):
        started, cpu_started = time.perf_counter(), time.process_time()
        function()
        wall.append(time.perf_counter() - started)
        cpu.append(time.process_time() - cpu_started)
    ordered = sorted(wall)
    return {
        "p50_ms": statistics.median(wall) * 1000,
        "p95_ms": ordered[math.ceil(0.95 * repeats) - 1] * 1000,
        "cpu_ms_total": sum(cpu) * 1000,
        "repeats": repeats,
    }


def benchmark(directory, revision, count=5000):
    if revision.startswith("-"):
        raise ValueError("invalid baseline revision")
    directory = Path(directory).resolve()
    baseline_source = subprocess.check_output(["git", "show", revision + ":platform/ailab_platform/storage.py"], cwd=ROOT)
    module = types.ModuleType("ailab_platform._baseline_storage")
    module.__package__ = "ailab_platform"
    exec(compile(baseline_source, "baseline-storage.py", "exec"), module.__dict__)
    legacy = module.LocalStorage(directory / "objects")
    started = time.perf_counter()
    for i in range(count):
        legacy.put_bytes(f"objects/{i:07d}", b"x" * 128)
    write_seconds = time.perf_counter() - started
    current = LocalStorage(directory / "objects")
    cursor, pages = None, 0
    started = time.perf_counter()
    while True:
        page = current.list_prefix("objects/", cursor=cursor, limit=1000)
        cursor, pages = page.cursor, pages + 1
        if cursor is None:
            break
    discovery_seconds = time.perf_counter() - started
    cursor = f"objects/{count // 2:07d}"

    def old_page():
        return legacy.list_prefix("objects/", cursor, 20)

    def new_page():
        return current.list_prefix("objects/", cursor, 20)

    assert [x.key for x in old_page()] == [x.key for x in new_page()]
    result = {
        "baseline": revision,
        "objects": count,
        "bytes_per_object": 128,
        "page_size": 20,
        "baseline_adapter_sha256": hashlib.sha256(baseline_source).hexdigest(),
        "current_adapter_sha256": hashlib.sha256((ROOT / "platform/ailab_platform/storage.py").read_bytes()).hexdigest(),
        "legacy_write_seconds": write_seconds,
        "one_time_discovery_seconds": discovery_seconds,
        "discovery_pages": pages,
        "legacy": measure(old_page),
        "indexed": measure(new_page),
    }
    current.close()
    (directory / "storage-baseline.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--baseline", default="97cb761f")
    parser.add_argument("--count", type=int, default=5000)
    options = parser.parse_args()
    benchmark(options.directory, options.baseline, options.count)
