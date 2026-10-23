"""Subprocess fixture: real durable API handback from all three scheduler slots."""

import asyncio
import json
import os

from fastapi.testclient import TestClient

from ailab_platform.agent import main
from ailab_platform.api.main import app
from ailab_platform.config import settings
from ailab_platform.queue import InMemoryQueue

client = TestClient(app)


class Platform:
    async def job_action(self, job_id, action, **body):
        response = await asyncio.to_thread(
            client.post, f"/internal/jobs/{job_id}/{action}", headers={"X-Service-Token": settings.service_token}, json=body
        )
        response.raise_for_status()
        return response.json()

    async def aclose(self):
        pass


async def scenario():
    queue = InMemoryQueue()
    payloads = json.loads(os.environ["FAULT_ENVELOPES"])
    for payload in payloads:
        queue.enqueue(f"{settings.run_queue}:{payload['mode']}", payload)
    started = []

    async def process(self, run_id, attempt):
        started.append(run_id)
        if len(started) == len(payloads):
            print("started", flush=True)
        try:
            await asyncio.sleep(3600)
        finally:
            # Exercise nonzero cancellation cleanup before the durable release.
            await asyncio.sleep(float(os.environ.get("FAULT_CLEANUP_SECONDS", "0.05")))

    main.AgentLoop.process = process
    main.get_queue = lambda: queue
    main.AsyncPlatformClient = Platform
    main._task = asyncio.create_task(main.consume_forever())
    main._chain_sigterm(asyncio.get_running_loop())
    try:
        await main._task
    except asyncio.CancelledError:
        print("drained", flush=True)


if __name__ == "__main__":
    asyncio.run(scenario())
