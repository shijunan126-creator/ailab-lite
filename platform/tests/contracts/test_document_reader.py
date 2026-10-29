"""Real container reader contract. Explicit disposable Docker context/image only."""

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ailab_platform.adapters.lease_store_memory import MemoryLeaseStore
from ailab_platform.broker.documents import DocumentReader
from ailab_platform.broker.service import LeaseManager
from ailab_platform.config import settings
from ailab_platform.sandbox.docker import DockerSandbox
from ailab_platform.storage import LocalStorage
from ailab_platform.worker.office import read
from tests.fakes import FakeRedis

IMAGE = os.environ.get("AILAB_TEST_DOCUMENT_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="AILAB_TEST_DOCUMENT_IMAGE requires a disposable Docker host")


def test_real_reader_formats_and_two_concurrent_users(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_sandbox_image", IMAGE)
    monkeypatch.setattr(settings, "sandbox_runtime", "runc")
    monkeypatch.setattr(settings, "sandbox_render_reserved_slots", 2)
    monkeypatch.setattr(settings, "max_sandboxes", 4)
    fixtures = Path(__file__).resolve().parents[3] / "vendor/zcode-office-runtime/fixtures"
    names = {"fdocx": "report.docx", "fpdf": "paper.pdf", "fxlsx": "budget.xlsx", "fpptx": "launch.pptx"}
    storage = LocalStorage(tmp_path / "objects")

    class Platform:
        def get_file(self, fid, **kwargs):
            assert kwargs == {"job": "document", "preview_job": "job-" + fid}
            p = fixtures / names[fid]
            return {"filename": p.name, "size": p.stat().st_size, "status": "pending"}

        def download_file(self, fid, target, **kwargs):
            assert kwargs["job"] == "document" and kwargs["preview_job"] == "job-" + fid
            target.write_bytes((fixtures / names[fid]).read_bytes())

        def prepare_document(self, fid, scan):
            return {"job_id": "job-" + fid}

        def read_document(self, fid, job_id):
            result = DocumentReader(manager).read(fid, job_id)
            runtime_metrics[fid] = result
            return result

    platform = Platform()
    import json
    import subprocess

    inspected = []
    containers = {}
    runtime_metrics = {}
    from threading import Barrier

    simultaneous = Barrier(2)

    class AuditedDocker(DockerSandbox):
        def acquire(self, *args, **kwargs):
            reference = super().acquire(*args, **kwargs)
            config = json.loads(subprocess.check_output(["docker", "inspect", reference], text=True))[0]
            host = config["HostConfig"]
            assert host["NetworkMode"] == "none" and host["Memory"] > 0
            assert "ALL" in host["CapDrop"]
            assert any("no-new-privileges" in option for option in host["SecurityOpt"])
            inspected.append(
                {
                    "network": host["NetworkMode"],
                    "memory": host["Memory"],
                    "cpu": host["NanoCpus"],
                    "cap_drop": host["CapDrop"],
                    "security_options": host["SecurityOpt"],
                }
            )
            containers[reference] = inspected[-1]
            return reference

    class ConcurrentManager(LeaseManager):
        def exec(self, handle, command, timeout=None):
            if "/opt/ailab-office/runner.mjs" in command:
                simultaneous.wait(timeout=10)
            return super().exec(handle, command, timeout)

        def store_file(self, lease, *args, **kwargs):
            metrics = self.provider.exec(lease["sandbox_id"], "cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/cpu.stat", 5)
            containers[lease["sandbox_id"]]["cgroup_after_parse"] = metrics.stdout
            return super().store_file(lease, *args, **kwargs)

    manager = ConcurrentManager(
        provider=AuditedDocker(),
        platform=platform,
        storage=storage,
        redis_client=FakeRedis(),
        store=MemoryLeaseStore(),
        start_background=False,
    )
    manager.recover()
    try:

        def parse(fid):
            started = time.monotonic()
            manifest = read(fid, platform, storage, tmp_path / fid, {"verdict": "clean"})
            assert manifest["status"] == "ready"
            text = storage.get_bytes(f"derived/{fid}/text.txt").decode()
            assert text
            return {
                "file": fid,
                "end_to_end_seconds": time.monotonic() - started,
                "sandbox_seconds": runtime_metrics[fid]["sandbox_seconds"],
                "chars": manifest["chars"],
                "units": manifest["total_units"],
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(parse, names))
        assert len({r["sha256"] for r in results}) == 4
        assert not manager.active_leases()
        evidence = Path(os.environ.get("AILAB_DOCUMENT_EVIDENCE_DIR", tmp_path))
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "real-reader.json").write_text(json.dumps({"results": results, "containers": inspected}, indent=2))
    finally:
        manager.close()
