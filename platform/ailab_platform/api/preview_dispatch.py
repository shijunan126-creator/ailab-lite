"""Bounded outbox dispatcher: crash after enqueue simply redelivers the same job id."""

import asyncio
import logging
import threading

from sqlalchemy import select

from ..db import SessionLocal
from ..models import OutboxMessage
from ..queue import get_queue

log = logging.getLogger("ailab.api.preview")
_dispatch_lock = threading.Lock()


def dispatch_preview_jobs(job_id: str | None = None) -> int:
    with _dispatch_lock, SessionLocal() as db:
        query = select(OutboxMessage).order_by(OutboxMessage.id).limit(20)
        if job_id:
            query = query.where(OutboxMessage.id == job_id)
        sent = 0
        for message in db.scalars(query):
            try:
                get_queue().enqueue(message.topic, message.payload)
            except Exception:
                log.warning("preview dispatch unavailable; retained in outbox")
                break
            db.delete(message)
            sent += 1
        db.commit()
        return sent


async def dispatch_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(dispatch_preview_jobs)
        except Exception:
            log.exception("preview outbox dispatch failed")
        await asyncio.sleep(1)
