"""Background catch-up for the conversation search index.

The ORM hooks keep the index current in the same transaction as every title, prompt and answer write. This loop exists
for rows written before migration 0070 and as a safety net for a write path that bypasses the hooks; it indexes one
bounded batch at a time and then sleeps, so it never competes with an interactive search.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import settings
from ..db import SessionLocal
from ..search_index import index_pending

log = logging.getLogger("ailab.api.search-index")


def _pass() -> bool:
    with SessionLocal() as db:
        return index_pending(db)


async def index_forever() -> None:
    if settings.env == "test":
        return
    while True:
        try:
            while await asyncio.to_thread(_pass):  # keep going while a backlog remains
                await asyncio.sleep(0.05)
        except Exception:
            log.exception("search index pass failed")
        await asyncio.sleep(settings.search_index_interval_seconds)
