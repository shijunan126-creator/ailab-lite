"""Backfill conversation and filename search in paced batches after API startup."""

from __future__ import annotations

import asyncio
import logging

from ..config import settings
from ..db import SessionLocal
from ..file_search import index_pending as index_pending_files
from ..search_index import index_pending

log = logging.getLogger("ailab.api.search-index")


def _pass(index) -> bool:
    with SessionLocal() as db:
        return index(db)


async def index_forever() -> None:
    if settings.env == "test":
        return

    async def catch_up(index):
        while await asyncio.to_thread(_pass, index):
            await asyncio.sleep(0.05)

    while True:
        try:
            await catch_up(index_pending)
        except Exception:
            log.exception("conversation search index pass failed")
        try:
            await catch_up(index_pending_files)
        except Exception:
            log.exception("file search index pass failed")
        await asyncio.sleep(settings.search_index_interval_seconds)
