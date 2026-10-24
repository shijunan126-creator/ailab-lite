"""Local bounded-parser / filesystem-injection probe; no model, network or developer DB."""

import json
import resource
import shutil
import statistics
import tempfile
import time
from pathlib import Path

from docx import Document
from PIL import Image
from pptx import Presentation

from ailab_platform.worker.isolate import execute
from tests.test_broker_lease import _ApiStub, _manager_with


def main():
    with tempfile.TemporaryDirectory(prefix="ailab-brand-measure-") as tmp:
        root = Path(tmp)
        logo = root / "logo.png"
        Image.new("RGB", (800, 200), "#123456").save(logo)
        pptx = root / "template.pptx"
        prs = Presentation()
        prs.slides.add_slide(prs.slide_layouts[6])
        prs.save(pptx)
        docx = root / "template.docx"
        doc = Document()
        for _ in range(15):
            doc.add_paragraph("Corporate template example")
        doc.save(docx)
        sources = {"logo": logo, "pptx": pptx, "docx": docx}
        result = {}
        for kind, source in sources.items():
            runs = []
            for i in range(5):
                started = time.perf_counter()
                out = execute("brand", source, root / f"{kind}-{i}", kind=kind, measure=True)
                runs.append({**out["measurement"], "wall_seconds": time.perf_counter() - started})
            result[kind] = {
                "input_bytes": source.stat().st_size,
                "cpu_seconds_median": statistics.median(r["cpu_seconds"] for r in runs),
                "wall_seconds_median": statistics.median(r["wall_seconds"] for r in runs),
                "peak_rss_bytes_max": max(r["peak_rss_bytes"] for r in runs),
                "output_bytes": sum(p.stat().st_size for p in (root / f"{kind}-0").iterdir()),
            }

        class API(_ApiStub):
            def get_run(self, run_id):
                return {**super().get_run(run_id), "brand": {"version": 1, "assets": {k: k for k in sources}}, "options": {"mode": "work"}}

            def download_file(self, file_id, target, *, max_bytes, **context):
                if sources[file_id].stat().st_size > max_bytes:
                    raise ValueError("limit")
                shutil.copyfile(sources[file_id], target)

        api = API({k: {"size": p.stat().st_size, "status": "ready"} for k, p in sources.items()}, [])
        manager, _provider, _ = _manager_with(api)
        elapsed, cpu = [], []
        try:
            for i in range(5):
                before = resource.getrusage(resource.RUSAGE_SELF)
                started = time.perf_counter()
                lease = manager.create(f"measure-{i}")
                elapsed.append(time.perf_counter() - started)
                after = resource.getrusage(resource.RUSAGE_SELF)
                cpu.append(after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime)
                manager.release(lease["lease_id"])
        finally:
            manager.close()
        result["injection_mock_filesystem"] = {
            "wall_seconds_median": statistics.median(elapsed),
            "cpu_seconds_median": statistics.median(cpu),
            "asset_bytes": sum(p.stat().st_size for p in sources.values()),
            "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
