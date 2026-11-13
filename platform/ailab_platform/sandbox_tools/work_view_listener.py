"""A lease-local, bounded LibreOffice listener for workspace views."""

import fcntl
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

try:
    from .sandbox_memory import retained_bytes
except ImportError:
    from sandbox_memory import retained_bytes

ROOT = Path("/tmp/ailab-work-view")
PIPE = "ailab_work_view"
IDLE_SECONDS = 120
PDF_LIMIT = 32 * 1024 * 1024
CACHE_LIMIT = 64 * 1024 * 1024
CACHE_ENTRIES = 4
PROFILE_RESTART = 81


def touch():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    (ROOT / "activity").write_text(str(time.monotonic()), encoding="ascii")


def memory_bytes(pid):
    return retained_bytes(Path(f"/proc/{pid}"))


def cache_entries():
    for abandoned in ROOT.glob("pdf-export-*"):
        if abandoned.is_dir() and not abandoned.is_symlink():
            shutil.rmtree(abandoned)
        else:
            abandoned.unlink()
    cache = ROOT / "pdf-cache"
    cache.mkdir(mode=0o700, exist_ok=True)
    if cache.is_symlink():
        raise RuntimeError("invalid_pdf_cache")
    entries = []
    for path in cache.iterdir():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.suffix != ".pdf":
            path.unlink()
        elif info.st_size > PDF_LIMIT or time.time() - info.st_mtime >= IDLE_SECONDS:
            path.unlink()
        else:
            entries.append((info.st_mtime, info.st_size, path))
    entries.sort()
    trim_cache(entries)
    return entries


def trim_cache(entries, incoming=0, slots=0):
    while entries and (len(entries) + slots > CACHE_ENTRIES or sum(item[1] for item in entries) + incoming > CACHE_LIMIT):
        entries.pop(0)[2].unlink()


def expire_cache(*, all_entries=False):
    with (ROOT / "cache.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        entries = cache_entries()
        if all_entries:
            for _, _, path in entries:
                path.unlink()


@contextmanager
def pdf_source(source, converter, revision):
    digest = hashlib.sha256(revision + source.suffix.encode("ascii"))
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    with (ROOT / "cache.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = cache_entries()
        cached = ROOT / "pdf-cache" / (digest.hexdigest() + ".pdf")
        if cached.exists():
            os.utime(cached, None)
            try:
                yield cached, True
            except Exception:
                cached.unlink(missing_ok=True)
                raise
            return
        with tempfile.TemporaryDirectory(prefix="pdf-export-", dir=ROOT) as temporary:
            converted = converter(source, "pdf", Path(temporary))
            size = converted.stat().st_size
            if 0 < size <= PDF_LIMIT:
                trim_cache(entries, size, slots=1)
                os.replace(converted, cached)
                try:
                    yield cached, False
                except Exception:
                    cached.unlink(missing_ok=True)
                    raise
            else:
                yield converted, False


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
        command = [
            "/usr/lib/libreoffice/program/soffice.bin",
            "--headless",
            "--invisible",
            "--norestore",
            f"-env:UserInstallation={(ROOT / 'profile').as_uri()}",
            f"--accept=pipe,name={PIPE};urp;",
        ]

        def launch():
            return subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        process = launch()
        initialized = False
        try:
            while True:
                code = process.poll()
                if code == PROFILE_RESTART and not initialized:
                    process.wait()
                    process = launch()
                    initialized = True
                    continue
                if code is not None:
                    break
                try:
                    last = float((ROOT / "activity").read_text(encoding="ascii"))
                    retained = memory_bytes(process.pid)
                except (OSError, ValueError, StopIteration):
                    break
                if time.monotonic() - last >= IDLE_SECONDS or retained > rss_limit:
                    break
                expire_cache()
                time.sleep(1)
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            expire_cache(all_entries=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--ensure":
        ensure(int(sys.argv[2]))
