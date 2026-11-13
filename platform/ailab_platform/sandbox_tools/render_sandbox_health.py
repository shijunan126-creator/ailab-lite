"""Clean a render job and fail closed unless its sandbox is safe to reuse."""

import json
import os
import shutil
import sys
from pathlib import Path

# Resolved from the script's own place (/workspace/.ailab) and TMPDIR so a provider that maps the sandbox
# into a host directory never makes this clean the host's /workspace or /tmp.
WORK = Path(__file__).resolve().parents[1]
TEMP = Path(os.environ.get("TMPDIR") or "/tmp")
KEEP_TOOLS = {"uno_convert.py", "render_sandbox_health.py", "sandbox_memory.py"}
KEEP_TEMP = {"lo-profile", "ailab-warm"}
# Every user file is deleted above; what remains is LibreOffice's own profile, which grows a few KiB per
# document (registry, recent list) for the same user. Destroying the sandbox for that defeats reuse.
PROFILE_GROWTH = 16 * 1024 * 1024


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


def listener_memory():
    try:
        from .sandbox_memory import retained_bytes
    except ImportError:
        from sandbox_memory import retained_bytes

    total, found = 0, False
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            command = (proc / "cmdline").read_bytes().replace(b"\0", b" ")
            total += retained_bytes(proc)
            if b"soffice.bin" in command and b"pipe,name=ailab;urp;" in command:
                found = True
        except FileNotFoundError:
            continue
    if not found:
        raise RuntimeError("listener_not_alive")
    return total


def cleanup(baseline, memory_limit, isolated=True):
    if not all((clear(WORK / "inputs", set()), clear(WORK / ".ailab", KEEP_TOOLS), clear(TEMP, KEEP_TEMP))):
        raise RuntimeError("cleanup_failed")
    if not isolated:
        # A host-backed sandbox (mock) has no listener and shares the host filesystem: only the cleanup applies.
        return {"retained_bytes": 0, "tmp_used": 0}
    retained = listener_memory()
    used = used_tmp()
    if retained >= memory_limit or used > baseline + PROFILE_GROWTH:
        raise RuntimeError(f"health_limit retained_bytes={retained} memory_limit={memory_limit} tmp_used={used} tmp_baseline={baseline}")
    return {"retained_bytes": retained, "tmp_used": used}


if __name__ == "__main__":
    if sys.argv[1:] == ["baseline"]:
        print(json.dumps({"tmp_used": used_tmp()}))
    else:
        print(json.dumps(cleanup(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3:4] != ["0"])))
