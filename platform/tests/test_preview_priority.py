from ailab_platform.api.preview_dispatch import _fair_preview_order
from ailab_platform.queue import Message, job_topic
from ailab_platform.worker import main


def test_due_preview_jobs_round_robin_users_with_priority_first():
    from types import SimpleNamespace

    def item(name, file_id, *, priority=False, kind="preview"):
        return SimpleNamespace(id=name, payload={"kind": kind, "file_id": file_id, "priority": priority})

    jobs = [item("a1", "a"), item("a2", "a"), item("b1", "b"), item("urgent", "a", priority=True), item("other", "x", kind="file")]
    assert [job.id for job in _fair_preview_order(jobs, {"a": "user_a", "b": "user_b"})] == ["urgent", "a1", "b1", "a2", "other"]


def test_urgent_preview_arriving_during_background_read_goes_first(monkeypatch):
    urgent = job_topic("preview", priority=True)
    background = job_topic("preview")
    calls = []

    class Queue:
        def dequeue(self, topic, consumer, timeout, visibility):
            calls.append(topic)
            if len(calls) == 1:
                return None
            return Message(id=topic, payload={})

    seen = []

    def handle(queue, msg, topic):
        seen.append(topic)
        if len(seen) == 2:
            main._stop.set()

    main._stop.clear()
    monkeypatch.setattr(main, "get_queue", Queue)
    monkeypatch.setattr(main, "handle_message", handle)
    try:
        main._consume_previews("test")
    finally:
        main._stop.clear()
    assert calls == [urgent, background, urgent]
    assert seen == [urgent, background]
