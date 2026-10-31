"""Clean a render job and fail closed unless its sandbox is safe to reuse."""

import json
import os
import shutil
import sys
from pathlib import Path

WORK = Path("/workspace")
TEMP = Path("/tmp")
KEEP_TOOLS = {"uno_convert.py", "render_sandbox_health.py"}
KEEP_TEMP = {"lo-profile", "ailab-warm"}


def used_tmp():
    stat = os.statvfs(TEMP)
    return (stat.f_blocks - stat.f_bfree) * stat.f_frsize


def clear(path, keep):
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.name in keep or (path == TEMP and child.name.startswith("OSL_PIPE_")):
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    return all(child.name in keep or (path == TEMP and child.name.startswith("OSL_PIPE_")) for child in path.iterdir())


def listener_rss():
    total, found = 0, False
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            command = (proc / "cmdline").read_bytes().replace(b"\0", b" ")
            status = (proc / "status").read_text()
            total += next(int(line.split()[1]) * 1024 for line in status.splitlines() if line.startswith("VmRSS:"))
            if b"soffice.bin" in command and b"pipe,name=ailab;urp;" in command:
                found = True
        except (OSError, StopIteration, ValueError):
            continue
    if not found:
        raise RuntimeError("listener_not_alive")
    return total


def cleanup(baseline, rss_limit):
    if not all((clear(WORK / "inputs", set()), clear(WORK / ".ailab", KEEP_TOOLS), clear(TEMP, KEEP_TEMP))):
        raise RuntimeError("cleanup_failed")
    rss = listener_rss()
    used = used_tmp()
    if rss >= rss_limit or used > baseline:
        raise RuntimeError("health_limit")
    return {"rss": rss, "tmp_used": used}


if __name__ == "__main__":
    if sys.argv[1:] == ["baseline"]:
        print(json.dumps({"tmp_used": used_tmp()}))
    else:
        print(json.dumps(cleanup(int(sys.argv[1]), int(sys.argv[2]))))
