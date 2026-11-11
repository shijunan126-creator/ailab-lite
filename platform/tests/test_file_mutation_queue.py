"""One file at a time: concurrent tool calls must not interleave on the same path."""

from __future__ import annotations

import asyncio

import pytest

from ailab_platform.agent import file_mutation_queue
from ailab_platform.agent.file_mutation_queue import with_file_mutation_queue


def run(coroutines) -> list:
    async def main():
        return await asyncio.gather(*coroutines())

    return asyncio.run(main())


def test_the_same_key_is_serialized_in_arrival_order():
    order: list[str] = []

    async def first():
        async with with_file_mutation_queue("lease:/workspace/work/a.txt"):
            order.append("first:start")
            await asyncio.sleep(0.02)
            order.append("first:end")

    async def second():
        async with with_file_mutation_queue("lease:/workspace/work/a.txt"):
            order.append("second:start")
            order.append("second:end")

    async def third():
        async with with_file_mutation_queue("lease:/workspace/work/a.txt"):
            order.append("third:start")
            order.append("third:end")

    run(lambda: (first(), second(), third()))
    assert order == ["first:start", "first:end", "second:start", "second:end", "third:start", "third:end"]


def test_different_keys_run_in_parallel():
    order: list[str] = []

    async def work(name: str):
        async with with_file_mutation_queue(name):
            order.append(f"{name}:start")
            await asyncio.sleep(0.02)
            order.append(f"{name}:end")

    run(lambda: (work("a"), work("b")))
    # If one key blocked the other, "b" could not start before "a" ended.
    assert order.index("b:start") < order.index("a:end")


def test_a_failure_releases_the_queue():
    async def failing():
        async with with_file_mutation_queue("f"):
            raise RuntimeError("boom")

    async def following():
        async with with_file_mutation_queue("f"):
            return "ran"

    async def main():
        results = await asyncio.gather(failing(), following(), return_exceptions=True)
        return results

    first, second = asyncio.run(main())
    assert isinstance(first, RuntimeError) and second == "ran"
    assert not file_mutation_queue._queues


def test_the_last_release_drops_the_entry():
    async def once():
        async with with_file_mutation_queue("cleanup"):
            assert "cleanup" in file_mutation_queue._queues

    run(lambda: (once(), once()))
    assert file_mutation_queue._queues == {}


@pytest.mark.parametrize("key", ["", "lease:/workspace/work/a.txt"])
def test_keys_are_taken_verbatim(key):
    async def once():
        async with with_file_mutation_queue(key):
            return key in file_mutation_queue._queues

    assert run(lambda: (once(),))[0] is True
