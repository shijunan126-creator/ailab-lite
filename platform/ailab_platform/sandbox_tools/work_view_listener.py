"""A lease-local, bounded LibreOffice listener for workspace views."""

import fcntl
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/tmp/ailab-work-view")
PIPE = "ailab_work_view"
IDLE_SECONDS = 120


def touch():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    (ROOT / "activity").write_text(str(time.monotonic()), encoding="ascii")


def rss_bytes(pid):
    status = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
    return next(int(line.split()[1]) * 1024 for line in status.splitlines() if line.startswith("VmRSS:"))


def ensure(rss_limit):
    touch()
    with (ROOT / "lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        fcntl.flock(lock, fcntl.LOCK_UN)
    subprocess.Popen(
        [sys.executable, __file__, "--serve", str(rss_limit)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with (ROOT / "lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
        time.sleep(0.05)
    raise RuntimeError("listener failed to start")


def serve(rss_limit):
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (ROOT / "lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        process = subprocess.Popen(
            [
                "/usr/lib/libreoffice/program/soffice.bin",
                "--headless",
                "--invisible",
                "--norestore",
                f"-env:UserInstallation={(ROOT / 'profile').as_uri()}",
                f"--accept=pipe,name={PIPE};urp;",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            while process.poll() is None:
                try:
                    last = float((ROOT / "activity").read_text(encoding="ascii"))
                    rss = rss_bytes(process.pid)
                except (OSError, ValueError, StopIteration):
                    break
                if time.monotonic() - last >= IDLE_SECONDS or rss > rss_limit:
                    break
                time.sleep(1)
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--ensure":
        ensure(int(sys.argv[2]))
