"""Request API-owned thumbnail outbox intents in bounded keyset batches."""

from __future__ import annotations

import argparse
import logging

from ..clients import PlatformClient
from ..config import settings

log = logging.getLogger("ailab.worker.backfill")


def backfill_thumbnails(*, batch_size: int = 200) -> dict[str, int]:
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    client = PlatformClient(service="worker")
    cursor = ""
    enqueued = 0
    try:
        while True:
            batch = client.thumbnail_batch(cursor, batch_size)
            enqueued += batch["enqueued"]
            if batch["cursor"] is None:
                break
            cursor = batch["cursor"]
    finally:
        client.close()
    return {"enqueued": enqueued, "failed": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=200, choices=range(1, 1001), metavar="1..1000")
    args = parser.parse_args()
    if settings.queue_backend == "memory":
        parser.error("backfill requires a shared queue: configure AILAB_QUEUE_BACKEND=redis")
    logging.basicConfig(level=logging.INFO)
    result = backfill_thumbnails(batch_size=args.batch_size)
    print(result)
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
