"""Bounded independent Chat/Work admission and cancellation of every active task."""

import asyncio
import time

from ailab_platform.agent import main
from ailab_platform.config import settings
from ailab_platform.queue import InMemoryQueue


def test_slow_work_cannot_starve_chat_and_shutdown_cancels_all(monkeypatch):
    async def scenario():
        queue = InMemoryQueue()
        monkeypatch.setattr(main, "get_queue", lambda: queue)
        active, peaks, canceled = {"work": 0, "chat": 0}, {"work": 0, "total": 0}, []
        entered = asyncio.Event()
        chat = asyncio.Event()

        async def handle(loop, queue, msg, topic):
            mode = topic.rsplit(":", 1)[-1]
            active[mode] += 1
            peaks["work"] = max(peaks["work"], active["work"])
            peaks["total"] = max(peaks["total"], sum(active.values()))
            try:
                if mode == "chat":
                    chat.set()
                elif active[mode] == settings.agent_concurrency - settings.chat_reserved_slots:
                    entered.set()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                canceled.append(mode)
                raise
            finally:
                active[mode] -= 1

        monkeypatch.setattr(main, "handle_message", handle)
        for i in range(10):
            queue.enqueue(f"{settings.run_queue}:work", {"run_id": str(i)})
        consumer = asyncio.create_task(main.consume_forever())
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            started = time.monotonic()
            queue.enqueue(f"{settings.run_queue}:chat", {"run_id": "chat"})
            await asyncio.wait_for(chat.wait(), timeout=1)
            assert time.monotonic() - started < 1
            assert peaks == {"work": 2, "total": 3}
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        assert sorted(canceled) == ["chat", "work", "work"]
        assert active == {"work": 0, "chat": 0}

    asyncio.run(scenario())


def test_drain_deadline_collects_slow_cancel_results(monkeypatch):
    async def scenario():
        queue = InMemoryQueue()
        monkeypatch.setattr(main, "get_queue", lambda: queue)
        monkeypatch.setattr(settings, "drain_seconds", 0.03)
        entered = asyncio.Event()
        errors = []
        event_loop = asyncio.get_running_loop()
        previous = event_loop.get_exception_handler()
        event_loop.set_exception_handler(lambda loop, context: errors.append(context))

        async def handle(*args):
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                await asyncio.sleep(1)

        monkeypatch.setattr(main, "handle_message", handle)
        queue.enqueue(f"{settings.run_queue}:work", {"run_id": "slow-cleanup"})
        consumer = asyncio.create_task(main.consume_forever())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            started = time.monotonic()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            assert time.monotonic() - started < 0.2
            await asyncio.sleep(0.02)
            assert not errors
        finally:
            event_loop.set_exception_handler(previous)

    asyncio.run(scenario())
