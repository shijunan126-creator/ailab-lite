"""Bounded-memory LocalStorage probe; use a temporary directory, never the developer data store."""

import argparse
import hashlib
import json
import resource
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from ailab_platform.storage import LocalStorage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mib", type=int, choices=(50, 100), default=100)
    args = parser.parse_args()
    size = args.mib * 1024 * 1024
    chunk = b"x" * 65536
    rss_scale = 1 if sys.platform == "darwin" else 1024
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * rss_scale
    tracemalloc.start()
    with tempfile.TemporaryDirectory(prefix="ailab-storage-probe-") as tmp:
        storage = LocalStorage(Path(tmp))
        started = time.monotonic()
        storage.put_stream("probe/source", (chunk for _ in range(size // len(chunk))), max_bytes=size)
        wrote = time.monotonic()
        storage.copy("probe/source", "probe/frozen")
        copied = time.monotonic()
        digest = hashlib.sha256()
        received = 0
        with storage.open_stream("probe/frozen") as chunks:
            for part in chunks:
                received += len(part)
                digest.update(part)
        assert received == size and digest.hexdigest() == storage.info("probe/frozen").etag
        disk = sum(path.stat().st_size for path in Path(tmp).rglob("*") if path.is_file())
        assert storage.delete_prefix("probe/") == 2
        peak = tracemalloc.get_traced_memory()[1]
        assert peak < 8 * 1024 * 1024, f"streaming allocated {peak} bytes"
        print(
            json.dumps(
                {
                    "mib": args.mib,
                    "write_seconds": wrote - started,
                    "copy_seconds": copied - wrote,
                    "total_seconds": time.monotonic() - started,
                    "python_peak_bytes": peak,
                    "rss_growth_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * rss_scale - rss_before,
                    "disk_bytes_two_objects": disk,
                }
            )
        )


if __name__ == "__main__":
    main()
