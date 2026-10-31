"""Run the Office parser with a per-process RSS limit inside a shared render sandbox."""

import subprocess
import sys
import time
from pathlib import Path


def rss(pid):
    if sys.platform == "linux":
        status = Path(f"/proc/{pid}/status").read_text()
        return next(int(line.split()[1]) * 1024 for line in status.splitlines() if line.startswith("VmRSS:"))
    result = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=True)
    return int(result.stdout.strip()) * 1024


def run(command, timeout, rss_limit):
    process = subprocess.Popen(command)
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                return 124
            try:
                if rss(process.pid) > rss_limit:
                    return 137
            except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
                if process.poll() is None:
                    return 1
            time.sleep(0.05)
        return process.returncode
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


if __name__ == "__main__":
    try:
        sys.exit(run(sys.argv[3:], float(sys.argv[1]), int(sys.argv[2])))
    except FileNotFoundError:
        sys.exit(127)
