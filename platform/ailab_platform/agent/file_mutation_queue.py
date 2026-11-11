"""Serialize writes to one file so two tool calls cannot interleave on it.

Pi keys its queue by ``realpath``, so a symlink and its target share a lock. A
workspace here is only reachable through the Broker, where resolving the real
path would cost a round trip per write; the key is the lease plus the normalized
path instead. Two aliases of one inode therefore queue separately, which is
acceptable because the only parallel writers in a run (``run_subtasks`` children)
work on distinct files.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class _Entry:
    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0


_queues: dict[str, _Entry] = {}


@asynccontextmanager
async def with_file_mutation_queue(key: str):
    """Hold the queue for ``key``; different keys run in parallel, one key runs in arrival order."""
    entry = _queues.get(key)
    if entry is None:
        entry = _queues[key] = _Entry()
    entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.users -= 1
        if not entry.users and _queues.get(key) is entry:
            del _queues[key]
