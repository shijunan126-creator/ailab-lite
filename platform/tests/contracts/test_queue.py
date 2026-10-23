"""Identical delivery tests for memory and Redis Streams (including XAUTOCLAIM)."""

import os
import uuid

import pytest

from ailab_platform.queue import InMemoryQueue, RedisQueue


@pytest.fixture(params=["memory", "redis"])
def queue(request):
    if request.param == "memory":
        yield InMemoryQueue(), "contract"
        return
    if not (url := os.environ.get("AILAB_TEST_REDIS_URL")):
        pytest.skip("AILAB_TEST_REDIS_URL not set")
    q = RedisQueue(url)
    name = "contract:" + uuid.uuid4().hex
    try:
        yield q, name
    finally:
        q.client.delete(name)
        q.client.close()


def test_redelivery_ack_and_duplicate_delivery(queue):
    q, name = queue
    q.enqueue(name, {"id": "work"})
    first = q.dequeue(name, "crashed", timeout=0)
    assert first.deliveries == 1
    assert q.dequeue(name, "other", timeout=0) is None
    again = q.dequeue(name, "recovery", timeout=0, visibility_seconds=0)
    assert again.id == first.id and again.deliveries == 2
    q.ack(name, again)
    q.ack(name, first)
    assert q.length(name) == 0
    q.enqueue(name, {"id": "work"})
    duplicate = q.dequeue(name, timeout=0)
    assert duplicate.id != first.id and duplicate.payload == first.payload
    q.ack(name, duplicate)


def test_payload_is_serialized_at_enqueue_and_delivery(queue):
    q, name = queue
    payload = {"nested": [1]}
    q.enqueue(name, payload)
    payload["nested"].append(2)
    first = q.dequeue(name, timeout=0)
    assert first.payload == {"nested": [1]}
    first.payload["nested"].append(3)
    assert q.dequeue(name, timeout=0, visibility_seconds=0).payload == {"nested": [1]}


def test_xautoclaim_scan_cursor_progresses_past_live_pending(queue):
    q, name = queue
    if not isinstance(q, RedisQueue):
        pytest.skip("Redis scan cursor contract")
    for i in range(15):
        q.enqueue(name, {"i": i})
    messages = [q.dequeue(name, timeout=0) for _ in range(15)]
    q.client.xclaim(name, q.GROUP, "old", 0, [messages[-1].id], idle=60_000)
    found = None
    for _ in range(4):
        found = q.dequeue(name, "recovery", timeout=0, visibility_seconds=30)
        if found:
            break
    assert found and found.id == messages[-1].id
