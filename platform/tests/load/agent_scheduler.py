"""Single-process scheduler load with real client pools and a delayed runtime substitute.

Run from platform: PYTHONPATH=. python tests/load/agent_scheduler.py --work-seconds 60
This measures scheduler/client overhead, not production model contexts or Broker RSS.
"""

import argparse
import asyncio
import json
import resource
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta

from tests import conftest  # noqa: F401 -- isolate settings before service imports

# isort: split
from ailab_platform.agent import main
from ailab_platform.config import settings
from ailab_platform.contracts.jobs import JobEnvelope
from ailab_platform.queue import InMemoryQueue


def rss_mib():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024 if sys.platform == "darwin" else 1024)


async def measure(work_seconds):
    queue = InMemoryQueue()
    main.get_queue = lambda: queue

    class Claims:
        async def job_action(self, job_id, action, **body):
            return {"state": "claimed", "token": job_id, "attempt": 1, "attempt_id": job_id} if action == "claim" else {"ok": True}

        async def get_run_state(self, run_id):
            return {"status": "succeeded"}

        async def aclose(self):
            pass

    main.AsyncPlatformClient = Claims

    def enqueue(run_id, mode):
        payload = JobEnvelope(
            job_id=run_id,
            run_id=run_id,
            kind="run",
            mode=mode,
            enqueued_at=datetime.now(UTC),
            deadline=datetime.now(UTC) + timedelta(seconds=work_seconds + 60),
        )
        queue.enqueue(f"{settings.run_queue}:{mode}", payload.model_dump(mode="json"))

    waits, sent = [], {}
    running, peak = 0, 0
    work_ready, work_done = asyncio.Event(), asyncio.Event()
    baseline = rss_mib()
    cpu_start = resource.getrusage(resource.RUSAGE_SELF)
    started = time.monotonic()
    chats_done = asyncio.Event()

    async def process(self, run_id, attempt=1, *, attempt_id):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            if run_id.startswith("work"):
                if running == 2:
                    work_ready.set()
                await asyncio.sleep(work_seconds)
                work_done.set()
            else:
                waits.append(time.monotonic() - sent[run_id])
                await asyncio.sleep(0.005)
                if len(waits) == 100:
                    chats_done.set()
        finally:
            running -= 1

    main.AgentLoop.process = process
    consumer = asyncio.create_task(main.consume_forever())
    for index in range(2):
        enqueue(f"work-{index}", "work")
    await asyncio.wait_for(work_ready.wait(), 3)
    try:
        for index in range(100):
            rid = f"chat-{index}"
            sent[rid] = time.monotonic()
            enqueue(rid, "chat")
            await asyncio.sleep(work_seconds / 110)
        await asyncio.wait_for(chats_done.wait(), 3)
        await asyncio.wait_for(work_done.wait(), work_seconds + 3)
        result = {
            "work_seconds": work_seconds,
            "chat_count": len(waits),
            "peak_active": peak,
            "chat_queue_p95_seconds": statistics.quantiles(waits, n=100)[94],
            "rss_baseline_mib": baseline,
            "rss_peak_mib": rss_mib(),
            "rss_increment_mib": rss_mib() - baseline,
            "cpu_seconds": (
                resource.getrusage(resource.RUSAGE_SELF).ru_utime
                + resource.getrusage(resource.RUSAGE_SELF).ru_stime
                - cpu_start.ru_utime
                - cpu_start.ru_stime
            ),
            "wall_seconds": time.monotonic() - started,
        }
        assert peak <= settings.agent_concurrency
        assert result["chat_queue_p95_seconds"] < 1
        return result
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-seconds", type=float, default=60)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(measure(args.work_seconds)), indent=2))
