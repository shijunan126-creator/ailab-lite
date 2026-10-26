"""Authenticated browser supervisor; each request gets a disposable process tree."""

import asyncio
import contextlib
import hmac
import json
import os
import signal
import sys
from pathlib import Path

from aiohttp import web

TOKEN = os.environ.get("AILAB_CRAWLER_TOKEN", "")
PROXY = os.environ.get("AILAB_CRAWLER_PROXY", "")
PORT = int(os.environ.get("AILAB_CRAWLER_PORT", "8090"))
READ_TIMEOUT_SECONDS = int(os.environ.get("AILAB_CRAWLER_PAGE_TIMEOUT_MS", "20000")) / 1000 + 10
MAX_PAGES = int(os.environ.get("AILAB_CRAWLER_MAX_PAGES", "1"))
MAX_CHARS = 60_000
slots = asyncio.Semaphore(MAX_PAGES)


def worker_command():
    return [sys.executable, str(Path(__file__).with_name("render.py"))]


def descendants(pid):
    parents = {}
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            try:
                parents[int(path.name)] = int((path / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
    found = {pid}
    while True:
        extra = {child for child, parent in parents.items() if parent in found} - found
        if not extra:
            return found
        found.update(extra)


def send(pid, sig):
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, sig)


async def stop_worker(process):
    # Freeze the root first, then its descendants until none can fork or reparent.
    # The worker is a subreaper, so detached Chromium/crashpad children stay here.
    send(process.pid, signal.SIGSTOP)
    frozen = set()
    for _ in range(256):
        current = descendants(process.pid)
        for pid in current - frozen:
            send(pid, signal.SIGSTOP)
        if current <= frozen:
            break
        frozen.update(current)
    for pid in frozen - {process.pid}:
        send(pid, signal.SIGKILL)
    send(process.pid, signal.SIGKILL)
    # Also catch a driver inheriting the session if the worker exited unexpectedly.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


async def render_page(url, limit):
    process = None
    launch = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *worker_command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=1024 * 1024,
        )
    )
    try:
        async with asyncio.timeout(READ_TIMEOUT_SECONDS):
            # Cancellation must not lose a process created before its handle arrives.
            process = await asyncio.shield(launch)
            process.stdin.write(json.dumps({"url": url, "max_chars": limit}).encode() + b"\n")
            await process.stdin.drain()
            line = await process.stdout.readline()
            if not line:
                raise RuntimeError("render worker exited without a result")
            result = json.loads(line)
            return web.json_response(result["body"], status=result["status"])
    finally:
        async with asyncio.timeout(5):
            if process is None:
                process = await asyncio.shield(launch)
            cleanup = asyncio.create_task(stop_worker(process))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise


async def read(request):
    if not TOKEN or not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {TOKEN}"):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
        url = str(body["url"])
        limit = max(1000, min(int(body.get("max_chars") or 20_000), MAX_CHARS))
    except (ValueError, KeyError, TypeError):
        return web.json_response({"error": "expected {url, max_chars}"}, status=400)
    if slots.locked():
        return web.json_response({"error": "crawler is busy"}, status=503)
    async with slots:
        try:
            return await render_page(url, limit)
        except TimeoutError:
            return web.json_response({"error": "page did not finish loading"}, status=504)
        except Exception as exc:
            return web.json_response({"error": f"render failed: {type(exc).__name__}"}, status=502)


async def health(_: web.Request) -> web.Response:
    # A live HTTP server cannot render when its cgroup has no room for Chromium.
    try:
        maximum = Path("/sys/fs/cgroup/pids.max").read_text().strip()
        current = int(Path("/sys/fs/cgroup/pids.current").read_text())
        if maximum != "max" and int(maximum) - current < 16:
            return web.json_response({"ok": False, "error": "insufficient process capacity"}, status=503)
    except (OSError, ValueError):
        pass  # cgroup v1 / an unconstrained local process has no v2 counter.
    return web.json_response({"ok": True})


def main() -> None:
    if not TOKEN or not PROXY:
        raise SystemExit("AILAB_CRAWLER_TOKEN and AILAB_CRAWLER_PROXY are required")
    app = web.Application(client_max_size=16 * 1024)
    app.router.add_post("/read", read)
    app.router.add_get("/healthz", health)
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None)


if __name__ == "__main__":
    main()
