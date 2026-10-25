"""Executed inside the sandbox via exec (stdlib only). Not imported by platform services."""

import codecs
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
    started = time.monotonic()
    child = subprocess.Popen(["/bin/sh", "-c", payload["cmd"]], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = started + min(payload["max_seconds"], max(0, payload["expires_at"] - time.time()))
    streams = {child.stdout: "stdout", child.stderr: "stderr"}
    tails = {"stdout": "", "stderr": ""}
    prefixes, counts = {"stdout": "", "stderr": ""}, {"stdout": 0, "stderr": 0}
    decoders = {name: codecs.getincrementaldecoder("utf-8")(errors="replace") for name in prefixes}
    total = 0
    with (directory / "log").open("w+b", buffering=0) as log:
        while streams and time.monotonic() < deadline:
            ready = select.select(list(streams), [], [], min(0.25, max(0, deadline - time.monotonic())))[0]
            for stream in ready:
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    name = streams[stream]
                    tail = decoders[name].decode(b"", final=True)
                    counts[name] += len(tail)
                    prefixes[name] = (prefixes[name] + tail)[:1000]
                    tails[name] = (tails[name] + tail)[-8000:]
                    streams.pop(stream)
                    stream.close()
                    continue
                name = streams[stream]
                text = decoders[name].decode(chunk)
                counts[name] += len(text)
                prefixes[name] = (prefixes[name] + text)[:1000]
                tails[name] = (tails[name] + text)[-8000:]
                fcntl.flock(log, fcntl.LOCK_EX)
                try:
                    while chunk:
                        start = total % cap
                        part, chunk = chunk[: cap - start], chunk[cap - start :]
                        log.seek(start)
                        log.write(part)
                        total += len(part)
                    put(directory / "progress", {"total_bytes": total, "last_output_at": time.time()})
                finally:
                    fcntl.flock(log, fcntl.LOCK_UN)
        try:
            code = child.wait(timeout=max(0.001, deadline - time.monotonic()))
            if streams:
                raise subprocess.TimeoutExpired(payload["cmd"], payload["max_seconds"])
        except subprocess.TimeoutExpired:
            put(directory / "exit", {"exit_code": -9})
            os.killpg(os.getpgrp(), signal.SIGKILL)
        result = {"exit_code": code, "duration_ms": int((time.monotonic() - started) * 1000)}
        for name, prefix in prefixes.items():
            result[name] = tails[name]
            if counts[name] > 8000:
                result[name] = (
                    prefix
                    + (f"\n...[elided {counts[name] - 9000} chars]...\n" if counts[name] > 9000 else "")
                    + tails[name][-min(8000, counts[name] - 1000) :]
                )
        put(directory / "exit", result)


def wire_stream(text):
    # Two streams get 30KB each; leave >4KB for fixed protocol metadata.
    # JSON escaping costs at most six bytes per character. Preserve the full
    # 8000-character tail for ordinary/CJK text, adapt only pathological controls.
    if len(json.dumps(text, ensure_ascii=False).encode("utf-8")) <= 30000:
        return text
    head, tail = text[:1000], text[-8000:]
    while len(json.dumps(head + "\n...[wire elided]...\n" + tail, ensure_ascii=False).encode("utf-8")) > 30000:
        if head:
            head = head[: len(head) // 2]
        else:
            tail = tail[len(tail) // 10 + 1 :]
    return head + "\n...[wire elided]...\n" + tail


def control(payload):
    ROOT.mkdir(mode=0o700, exist_ok=True)
    directory = ROOT / payload["handle"]
    if payload["action"] == "kill_all":
        # Agent-side lists can contain pruned handles; the lease-local registry is authoritative.
        killed = 0
        with (ROOT / "lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for candidate in ROOT.glob("*/meta"):
                if (candidate.parent / "exit").exists():
                    continue
                meta = json.loads(candidate.read_text())
                if alive(meta):
                    try:
                        os.killpg(meta["pid"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    killed += 1
                put(candidate.parent / "exit", {"exit_code": -9})
        return {"ok": True, "running": False, "killed": killed}
    if payload["action"] == "start":
        with (ROOT / "lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            import shutil

            candidates = sorted((p for p in ROOT.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
            # Retain recent completed handles too; prune only exited/stale entries, oldest first.
            for candidate in candidates[:]:
                if len(candidates) < payload["max_jobs"]:
                    break
                if (
                    (candidate / "exit").exists()
                    or not (candidate / "meta").exists()
                    or not alive(json.loads((candidate / "meta").read_text()))
                ):
                    shutil.rmtree(candidate)
                    candidates.remove(candidate)
            if len(candidates) >= payload["max_jobs"]:
                return {"ok": False, "error": "后台命令并发数已达上限，请先等待或终止已有命令"}
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
            meta = {
                "pid": worker.pid,
                "starttime": process_starttime(worker.pid),
                "log_bytes": payload["log_bytes"],
                "started_at": time.time(),
            }
            put(directory / "meta", meta)
        payload = {**payload, "action": "wait", "initial": True}
    if not (directory / "meta").exists():
        return {"ok": False, "running": False, "error": "后台命令已不存在；租约恢复后请检查工作区，勿直接重复启动"}
    meta = json.loads((directory / "meta").read_text())
    payload["log_bytes"] = meta["log_bytes"]
    if payload["action"] == "kill" and not (directory / "exit").exists():
        if alive(meta):
            os.killpg(meta["pid"], signal.SIGKILL)
        put(directory / "exit", {"exit_code": -9})
    deadline = time.monotonic() + min(payload["seconds"], 300)
    while payload["action"] == "wait" and not (directory / "exit").exists() and alive(meta) and time.monotonic() < deadline:
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    total, output, start = 0, b"", payload["offset"]
    last_output_at = meta.get("started_at", time.time())
    if (directory / "log").exists():
        with (directory / "log").open("rb") as log:
            fcntl.flock(log, fcntl.LOCK_SH)
            progress = json.loads((directory / "progress").read_text()) if (directory / "progress").exists() else {}
            total = progress.get("total_bytes", 0)
            last_output_at = progress.get("last_output_at", last_output_at)
            start = max(payload["offset"], total - min(4000, payload["log_bytes"]))
            if start < total:
                pos = start % payload["log_bytes"]
                log.seek(pos)
                output = log.read(min(total - start, payload["log_bytes"] - pos))
                if len(output) < total - start:
                    log.seek(0)
                    output += log.read(total - start - len(output))
    completed = json.loads((directory / "exit").read_text()) if (directory / "exit").exists() else {}
    if isinstance(completed, int):  # v1 handles may finish while an Agent is being upgraded.
        completed = {"exit_code": completed}
    code = completed.get("exit_code")
    running = code is None and alive(meta)
    if payload.get("initial") and code is not None:
        return {"ok": code == 0, **{key: wire_stream(value) if key in {"stdout", "stderr"} else value for key, value in completed.items()}}
    return {
        "ok": running or code is not None,
        "error": "" if running or code is not None else "后台进程退出状态丢失，请先检查文件",
        "handle": payload["handle"],
        "running": running,
        "exit_code": code,
        "output_tail": output.decode(errors="replace"),
        "idle_seconds": round(max(0, time.time() - last_output_at), 3),
        "hint": "命令仍在运行；用 exec_wait 等待并检查输出进展。" if running else "",
        "total_bytes": total,
        "truncated": start > payload["offset"],
    }


if __name__ == "__main__":
    request = json.loads(sys.argv[1])
    if request["action"] == "supervise":
        supervise(request, ROOT / request["handle"])
    else:
        print(json.dumps(control(request), ensure_ascii=False))
