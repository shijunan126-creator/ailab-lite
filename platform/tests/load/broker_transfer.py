"""Opt-in Broker RSS probe, run in a disposable Linux container (see review).

Real HTTP source + real OpenSandbox transfer + Local storage. API metadata and
artifact registration are fixtures; this measures Broker I/O, not full-stack latency.
"""

import argparse
import hashlib
import json
import os
import resource
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ailab_platform.adapters.lease_store_memory import MemoryLeaseStore
from ailab_platform.broker.service import LeaseManager
from ailab_platform.clients import PlatformClient
from ailab_platform.config import settings
from ailab_platform.sandbox.opensandbox import OpenSandboxProvider
from ailab_platform.storage import LocalStorage

MIB = 1024 * 1024


def rss():
    return int(next(line.split()[1] for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("VmRSS:"))) * 1024


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mib", type=int, required=True)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings.max_sandboxes = 4
    settings.sandbox_render_reserved_slots = 0
    settings.sandbox_memory_bytes = 1536 * MIB
    settings.sandbox_total_memory_bytes = 3 * 1024 * MIB
    settings.sandbox_cpu = 0.5
    settings.max_upload_bytes = 100 * MIB
    settings.max_artifact_bytes = 100 * MIB
    settings.sandbox_image = "ailab-f8-sandbox"
    settings.sandbox_ttl_seconds = 600
    with tempfile.TemporaryDirectory(dir=settings.data_dir) as root:
        root = Path(root)
        source = root / "source"
        with source.open("wb") as stream:
            # Larger than gzip's history window so snapshots stay close to the
            # source size; highly compressible zeros would hide that path.
            block = os.urandom(65536)
            for _ in range(args.mib * 16):
                stream.write(block)
        with source.open("rb") as stream:
            expected = hashlib.file_digest(stream, "sha256").hexdigest()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(source.stat().st_size))
                self.end_headers()
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, self.wfile, 65536)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        class API(PlatformClient):
            def get_run(self, run_id):
                return {"id": run_id, "user_id": run_id, "input_file_ids": ["input"]}

            def get_file(self, *args, **kwargs):
                return {"status": "ready", "filename": "input.bin"}

            def append_event(self, *args, **kwargs):
                return {}

            def commit_artifact(self, payload):
                assert payload["size"] == args.mib * MIB and payload["sha256"] == expected
                return payload

        provider = OpenSandboxProvider()
        api = API(base_url=f"http://127.0.0.1:{server.server_port}", service="broker")
        manager = LeaseManager(
            provider=provider,
            platform=api,
            storage=LocalStorage(root / "objects"),
            redis_client=object(),
            store=MemoryLeaseStore(),
            start_background=False,
        )
        manager.recover()  # HTTP service lifespan completes recovery before accepting concurrent requests.
        samples, stop = [], threading.Event()
        baseline = rss()

        def sample():
            while not stop.is_set():
                spool = 0
                for local in manager._transfer_root.glob("*/file"):
                    try:
                        spool += local.stat().st_size
                    except FileNotFoundError:
                        pass
                samples.append({"elapsed": round(time.monotonic() - started, 4), "rss": rss(), "spool_bytes": spool})
                stop.wait(0.02)

        def workload(index):
            began = time.monotonic()
            lease = manager.create(f"probe-{index}")
            try:
                injected = time.monotonic()
                result = provider.exec(lease["sandbox_id"], "cp /workspace/inputs/input.bin /workspace/outputs/result.bin", 60)
                assert result.exit_code == 0
                manager.commit(lease["lease_id"], "/workspace/outputs/result.bin")
                delivered = time.monotonic()
                if args.mib <= 50:
                    snapshot = manager.snapshot(lease["lease_id"])
                    manager._restore_workspace(lease, snapshot["key"])
                    assert lease["restored"]
                return {
                    "inject_seconds": injected - began,
                    "deliver_seconds": delivered - injected,
                    "snapshot_restore_seconds": time.monotonic() - delivered if args.mib <= 50 else None,
                }
            finally:
                manager.release(lease["lease_id"])

        started = time.monotonic()
        cpu_start = resource.getrusage(resource.RUSAGE_SELF)
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        try:
            with ThreadPoolExecutor(max_workers=args.parallel) as pool:
                operations = list(pool.map(workload, range(args.parallel)))
        finally:
            stop.set()
            sampler.join()
            api.close()
            provider._manager.close()
            server.shutdown()
        cpu_end = resource.getrusage(resource.RUSAGE_SELF)
        result = {
            "mib": args.mib,
            "parallel": args.parallel,
            "baseline_rss": baseline,
            "peak_rss": max(s["rss"] for s in samples),
            "wall_seconds": time.monotonic() - started,
            "cpu_seconds": cpu_end.ru_utime + cpu_end.ru_stime - cpu_start.ru_utime - cpu_start.ru_stime,
            "peak_spool_bytes": max(s["spool_bytes"] for s in samples),
            "cgroup_memory_peak": int(Path("/sys/fs/cgroup/memory.peak").read_text()),
            "cgroup_events": Path("/sys/fs/cgroup/memory.events").read_text(),
            "operations": operations,
            "samples": samples,
        }
        args.output.write_text(json.dumps(result, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k != "samples"}))


if __name__ == "__main__":
    main()
