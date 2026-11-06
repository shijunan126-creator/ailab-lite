"""Drive explicit OCR pages/regions without model calls or retained transport objects."""

import hashlib
import tempfile
import time
from datetime import datetime
from pathlib import Path

from ..clients import ClientError
from ..storage import get_storage
from .isolate import IsolationError, execute


def process(platform, operation_id):
    try:
        return _process(platform, operation_id)
    except (ValueError, IsolationError):
        result = platform.document_ocr_failed(operation_id)
        return result["status"] in {"ready", "failed", "canceled"}


def _process(platform, operation_id):
    storage = get_storage()
    for _ in range(21):
        state = platform.document_ocr_step(operation_id)
        if state["status"] in {"ready", "failed", "canceled"}:
            return True
        if state.get("waiting_for") or state["status"] == "pending":
            return False
        if state["status"] != "running" or not state.get("image"):
            return False
        deadline = datetime.fromisoformat(state["execution_deadline"]).timestamp()
        if deadline <= time.time():
            return False
        image = state["image"]
        with tempfile.TemporaryDirectory(prefix="ailab-document-ocr-") as temporary:
            root = Path(temporary)
            source = root / "input"
            digest, size = hashlib.sha256(), 0
            info = storage.info(image["key"])
            with source.open("wb") as out, storage.open_stream(image["key"]) as chunks:
                for chunk in chunks:
                    size += len(chunk)
                    if size > 2 * 1024**2 or time.time() >= deadline:
                        raise ValueError("ocr_image_transfer_limit")
                    digest.update(chunk)
                    out.write(chunk)
            if image.get("digest") and digest.hexdigest() != image["digest"]:
                raise ValueError("ocr_image_revision_changed")
            if storage.info(image["key"]).etag != info.etag or info.etag != image["revision"]:
                # For browser captures and page renders the revision is the
                # trusted SHA256; S3's ETag need not equal that digest.
                if not image.get("digest") or digest.hexdigest() != image["digest"]:
                    raise ValueError("ocr_image_revision_changed")
            output = root / "crop"
            remaining = min(30, deadline - time.time())
            if remaining <= 0:
                return False
            prepared = execute("ocr_image", source, output, timeout=remaining, memory_mb=192, region=image.get("region"))
            try:
                result = platform.document_ocr_image(
                    operation_id,
                    state["claim_token"],
                    state["image_index"],
                    output / prepared["image"],
                    {key: prepared[key] for key in ("image_sha256", "width", "height", "actual_region")},
                    deadline,
                )
            except ClientError as exc:
                if exc.status in {409, 502, 503, 504}:
                    # Re-read the durable step on redelivery; never resend an
                    # image whose inference may already have been confirmed.
                    return False
                raise
            if result["status"] in {"ready", "failed", "canceled"}:
                return True
    return False
