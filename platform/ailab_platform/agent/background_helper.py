"""Executed inside the sandbox via exec (stdlib only). Not imported by platform services."""

import fcntl
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/tmp/ailab-background")


def put(path, data):
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data))
    temporary.replace(path)


def process_starttime(pid):
    if sys.platform == "linux":
        return Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[19]
    # Host-process contract tests run on macOS; production Linux uses /proc without spawning ps.
    value = subprocess.check_output(["ps", "-o", "lstart=", "-p", str(pid)], text=True).strip()
    if not value:
        raise ProcessLookupError(pid)
    return value


def alive(meta):
    try:
        return process_starttime(meta["pid"]) == meta["starttime"]
    except (OSError, KeyError, subprocess.CalledProcessError):
        return False


def supervise(payload, directory):
    cap = payload["log_bytes"]
    child = subprocess.Popen(["/bin/sh", "-c", payload["cmd"]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + payload["max_seconds"]
    total = 0
    with (directory / "log").open("w+b", buffering=0) as log:
        while time.monotonic() < deadline:
            if not select.select([child.stdout], [], [], min(1, max(0, deadline - time.monotonic())))[0]:
                continue
            chunk = os.read(child.stdout.fileno(), 65536)
            if not chunk:
                break
            fcntl.flock(log, fcntl.LOCK_EX)
            try:
                while chunk:
                    start = total % cap
                    part, chunk = chunk[: cap - start], chunk[cap - start :]
                    log.seek(start)
                    log.write(part)
                    total += len(part)
                put(directory / "progress", {"total_bytes": total})
            finally:
                fcntl.flock(log, fcntl.LOCK_UN)
        else:
            # All descendants share this supervisor's process group.
            put(directory / "exit", -9)
            os.killpg(os.getpgrp(), signal.SIGKILL)
        try:
            code = child.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            put(directory / "exit", -9)
            os.killpg(os.getpgrp(), signal.SIGKILL)
        put(directory / "exit", code)
    child.stdout.close()


def control(payload):
    ROOT.mkdir(mode=0o700, exist_ok=True)
    directory = ROOT / payload["handle"]
    if payload["action"] == "start":
        with (ROOT / "lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            active = 0
            for candidate in ROOT.glob("*/meta"):
                if not (candidate.parent / "exit").exists() and alive(json.loads(candidate.read_text())):
                    active += 1
            if active >= payload["max_jobs"]:
                return {"ok": False, "error": "后台命令并发数已达上限，请先等待或终止已有命令"}
            # Finished jobs are explicitly removed on start; total disk is bounded by the job cap.
            import shutil

            for candidate in list(ROOT.iterdir()):
                if candidate.is_dir() and (
                    not (candidate / "meta").exists()
                    or (candidate / "exit").exists()
                    or not alive(json.loads((candidate / "meta").read_text()))
                ):
                    shutil.rmtree(candidate)
            directory.mkdir(mode=0o700)
            source = sys.argv[0] if sys.argv[0] != "-c" else None
            # The same helper source was supplied in the command; persist it only in this lease.
            helper = ROOT / "helper.py"
            if source:
                helper.write_bytes(Path(source).read_bytes())
            else:
                helper.write_text(globals()["HELPER_SOURCE"])
            worker = subprocess.Popen(
                [sys.executable, str(helper), json.dumps({**payload, "action": "supervise"})],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            meta = {"pid": worker.pid, "starttime": process_starttime(worker.pid), "log_bytes": payload["log_bytes"]}
            put(directory / "meta", meta)
        return {"ok": True, "handle": payload["handle"], "running": True, "output": "", "total_bytes": 0}
    if not (directory / "meta").exists():
        return {"ok": False, "running": False, "error": "后台命令已不存在；租约恢复后请检查工作区，勿直接重复启动"}
    meta = json.loads((directory / "meta").read_text())
    payload["log_bytes"] = meta["log_bytes"]
    if payload["action"] == "kill" and not (directory / "exit").exists():
        if alive(meta):
            os.killpg(meta["pid"], signal.SIGKILL)
        put(directory / "exit", -9)
    deadline = time.monotonic() + min(payload["seconds"], 590)
    while payload["action"] == "wait" and not (directory / "exit").exists() and alive(meta) and time.monotonic() < deadline:
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    total, output, start = 0, b"", payload["offset"]
    if (directory / "log").exists():
        with (directory / "log").open("rb") as log:
            fcntl.flock(log, fcntl.LOCK_SH)
            total = json.loads((directory / "progress").read_text())["total_bytes"] if (directory / "progress").exists() else 0
            start = max(payload["offset"], total - min(4000, payload["log_bytes"]))
            if start < total:
                pos = start % payload["log_bytes"]
                log.seek(pos)
                output = log.read(min(total - start, payload["log_bytes"] - pos))
                if len(output) < total - start:
                    log.seek(0)
                    output += log.read(total - start - len(output))
    code = json.loads((directory / "exit").read_text()) if (directory / "exit").exists() else None
    running = code is None and alive(meta)
    return {
        "ok": running or code is not None,
        "error": "" if running or code is not None else "后台进程退出状态丢失，请先检查文件",
        "handle": payload["handle"],
        "running": running,
        "exit_code": code,
        "output": output.decode(errors="replace"),
        "total_bytes": total,
        "truncated": start > payload["offset"],
    }


if __name__ == "__main__":
    request = json.loads(sys.argv[1])
    if request["action"] == "supervise":
        supervise(request, ROOT / request["handle"])
    else:
        print(json.dumps(control(request), ensure_ascii=False))
