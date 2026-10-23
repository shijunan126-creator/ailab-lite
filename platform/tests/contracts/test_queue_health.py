"""A stalled Redis socket must fit within the deployment readiness deadline."""

import socketserver
import threading

from ailab_platform.queue import RedisQueue


def test_redis_health_ping_has_a_short_deadline():
    stop = threading.Event()

    class StalledRedis(socketserver.BaseRequestHandler):
        def handle(self):
            stop.wait(5)

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), StalledRedis)
    server.daemon_threads = True
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    queue = RedisQueue(f"redis://127.0.0.1:{server.server_address[1]}/0")
    finished = threading.Event()
    outcome = []

    def probe():
        outcome.append(queue.ping())
        finished.set()

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    try:
        assert finished.wait(3), "readiness ping exceeded its three-second budget"
        assert outcome == [False]
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
        thread.join(timeout=1)


def test_concurrent_health_pings_do_not_report_a_healthy_redis_as_down():
    """Readiness, liveness and metrics may ping at the same moment; a full pool must not read as an outage."""
    import os

    import pytest

    url = os.environ.get("AILAB_TEST_REDIS_URL")
    if not url:
        pytest.skip("set AILAB_TEST_REDIS_URL to a throwaway Redis")
    queue = RedisQueue(url)
    results, start = [], threading.Barrier(8)

    def probe():
        start.wait()
        results.append(queue.ping())

    threads = [threading.Thread(target=probe) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert results == [True] * 8
