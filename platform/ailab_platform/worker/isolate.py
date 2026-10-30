"""Disposable parser processes. The parent never imports an untrusted-file parser."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

MAX_OUTPUT_BYTES = 16 * 1024 * 1024


class IsolationError(RuntimeError):
    pass


def execute(operation: str, source: Path, output: Path, *, timeout: float | None = None, memory_mb: int | None = None, **options) -> dict:
    from ..config import settings

    output.mkdir(parents=True, exist_ok=True)
    spec = output / "job.json"
    output_limit = 32 * 1024 * 1024 if operation == "skill_unpack" else MAX_OUTPUT_BYTES
    spec.write_text(
        json.dumps(
            {
                "operation": operation,
                "source": str(source),
                "output": str(output),
                "options": options,
                "memory_mb": memory_mb or settings.parse_memory_mb,
                "cpu_seconds": settings.parse_cpu_seconds,
                "output_limit": output_limit,
            }
        )
    )
    started = time.monotonic()
    with open(output / "stderr.log", "wb") as errors:
        proc = subprocess.Popen(
            [sys.executable, "-m", "ailab_platform.worker.isolate", str(spec)],
            stdout=subprocess.DEVNULL,
            stderr=errors,
            start_new_session=True,
            env={key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ},
        )
        try:
            while proc.poll() is None:
                if time.monotonic() - started > (timeout or settings.parse_timeout_seconds):
                    raise IsolationError("timeout")
                if sum(p.stat().st_size for p in output.iterdir() if p.is_file()) > output_limit:
                    raise IsolationError("output_limit")
                # macOS ignores RLIMIT_AS; use an RSS watchdog for dev in addition to CPU/wall/file limits.
                if sys.platform == "darwin":
                    rss = subprocess.run(
                        ["ps", "-o", "rss=", "-p", str(proc.pid)], capture_output=True, text=True, timeout=1
                    ).stdout.strip()
                    if rss and int(rss) > (memory_mb or settings.parse_memory_mb) * 1024:
                        raise IsolationError("memory_limit")
                time.sleep(0.05)
            if sum(p.stat().st_size for p in output.iterdir() if p.is_file()) > output_limit:
                raise IsolationError("output_limit")
            result = output / "result.json"
            if not result.exists():
                raise IsolationError(
                    "output_limit"
                    if proc.returncode == -signal.SIGXFSZ
                    else "cpu_limit"
                    if proc.returncode == -signal.SIGXCPU
                    else "resource_limit"
                    if proc.returncode
                    else "invalid_output"
                )
            if result.stat().st_size > MAX_OUTPUT_BYTES:
                raise IsolationError("output_limit")
            data = json.loads(result.read_text())
            if data.get("error"):
                raise IsolationError(data["error"])
            return data
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def _child(spec: dict) -> None:
    import resource

    memory = spec["memory_mb"] * 1024 * 1024
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (spec["cpu_seconds"], spec["cpu_seconds"] + 1))
    output_limit = spec.get("output_limit", MAX_OUTPUT_BYTES)
    resource.setrlimit(resource.RLIMIT_FSIZE, (output_limit, output_limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from .parsers import run

    try:
        result = run(spec["operation"], Path(spec["source"]), Path(spec["output"]), spec["options"])
    except MemoryError:
        result = {"error": "memory_limit"}
    except Exception as exc:
        # libxml reports failed C allocations as XMLSyntaxError, not MemoryError.
        # Use its structured error code; malformed XML must still be "corrupt".
        xml_oom = any(getattr(entry, "type_name", "") == "ERR_NO_MEMORY" for entry in getattr(exc, "error_log", ()))
        error = "memory_limit" if xml_oom else str(exc) if str(exc) in {"archive_limit", "pixel_limit", "input_limit"} else "corrupt"
        result = {"error": error}
    if spec["options"].get("measure"):
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result["measurement"] = {
            "peak_rss_bytes": usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024,
            "cpu_seconds": usage.ru_utime + usage.ru_stime,
        }
    (Path(spec["output"]) / "result.json").write_text(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    _child(json.loads(Path(sys.argv[1]).read_text()))
