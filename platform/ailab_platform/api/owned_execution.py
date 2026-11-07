"""Keep a request-owned execution and its DB session alive through cancellation cleanup."""

import asyncio
import logging

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

log = logging.getLogger("ailab.api.owned_execution")


async def execute_owned(request, execute, cancel):
    task = asyncio.create_task(run_in_threadpool(execute))

    async def stop():
        cancellation = asyncio.create_task(run_in_threadpool(cancel))
        while not cancellation.done():
            try:
                await asyncio.shield(cancellation)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not cancellation.cancelled() and cancellation.exception() is not None:
            log.warning("document cancellation could not be recorded; durable lease retains cleanup responsibility")
        while not task.done():
            try:
                await asyncio.shield(asyncio.gather(task, return_exceptions=True))
            except asyncio.CancelledError:
                continue

    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.25)
            if await request.is_disconnected():
                await stop()
                raise HTTPException(409, "document_request_disconnected")
        return await task
    finally:
        if not task.done():
            await stop()
