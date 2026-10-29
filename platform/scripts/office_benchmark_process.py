"""Supervise the fixed Office reader; untrusted parsers never run in the API."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from ailab_platform.worker.isolate import IsolationError

RUNTIME = Path(os.environ.get("AILAB_OFFICE_RUNTIME", Path(__file__).resolve().parents[2] / "vendor" / "zcode-office-runtime"))
PARSER_VERSION = "zcode-3c5b227-ailab1"
KINDS = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx"}


def execute(source: Path, output: Path, *, memory_mb: int = 256, timeout: float = 60) -> dict:
    kind = KINDS.get(source.suffix.lower())
    if not kind:
        raise IsolationError("unsupported")
    output.mkdir(parents=True, exist_ok=True)
    # Do not inherit model keys, service credentials or storage configuration.
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
    started = time.monotonic()
    with (output / "stderr.log").open("wb") as errors:
        try:
            proc = subprocess.Popen(
                ["node", f"--max-old-space-size={max(32, memory_mb - 96)}", str(RUNTIME / "runner.mjs"), str(source), str(output), kind],
                stdout=subprocess.DEVNULL,
                stderr=errors,
                env=env,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise IsolationError("office_runtime_unavailable") from exc
        try:
            while proc.poll() is None:
                if time.monotonic() - started > timeout:
                    raise IsolationError("timeout")
                if sum(p.stat().st_size for p in output.iterdir() if p.is_file()) > 17 * 1024 * 1024:
                    raise IsolationError("output_limit")
                rss = subprocess.run(["ps", "-o", "rss=", "-p", str(proc.pid)], capture_output=True, text=True, timeout=1).stdout.strip()
                if rss and int(rss) > memory_mb * 1024:
                    raise IsolationError("memory_limit")
                time.sleep(0.05)
            if proc.returncode or not (output / "manifest.json").exists():
                raise IsolationError("office_parse_failed")
            return json.loads((output / "manifest.json").read_text())
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
