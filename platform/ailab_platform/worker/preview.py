"""Preview jobs are independent of upload scanning and never alter file readiness."""

from __future__ import annotations

import hashlib
import logging
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ..clients import ClientError, PlatformClient
from ..config import settings
from ..contracts.file_preview import LOCAL_SHEETS, cached_page_preview, text_manifest
from ..queue import get_queue
from ..storage import get_storage
from .isolate import IsolationError, execute
from .pipeline import _images, download

_render_slot = threading.Lock()
log = logging.getLogger("ailab.worker.preview")


@contextmanager
def _try_render_slot():
    acquired = _render_slot.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _render_slot.release()


def process_preview(file_id: str, job_id: str, platform: PlatformClient | None = None) -> dict:
    if platform is None:
        with PlatformClient(service="worker") as owned:
            return process_preview(file_id, job_id, owned)
    queue = get_queue()
    started = time.monotonic()
    token = queue.acquire_lock(f"ailab:preview:{file_id}", 300)
    if token is None:
        return {"skipped": "in_flight"}
    slot_seconds = 0.0
    try:
        with _try_render_slot() as acquired:
            if not acquired:
                return {"skipped": "busy"}
            slot_seconds = time.monotonic() - started
            try:
                file = platform.get_file(file_id, job="preview")
            except ClientError as exc:
                if exc.status == 404:
                    return {"skipped": "deleted"}
                raise
            meta = file.get("meta") or {}
            if meta.get("preview_job_id") != job_id:
                return {"skipped": "superseded"}
            manifest = meta.get("preview") or {}
            scanned = file.get("status") == "pending" and (meta.get("scan") or {}).get("verdict") == "clean"
            if not (file.get("status") == "ready" or scanned) or manifest.get("status") != "pending":
                return {"skipped": "already_complete"}
            storage = get_storage()
            assets = {}
            render_lease_id = None
            render_generation = None
            try:
                visual_job = meta.get("ocr_render_job_id") == job_id
                reused = cached_page_preview(file_id, meta) if not visual_job and manifest.get("kind") == "pages" else None
                if reused and file.get("purpose") != "template":
                    manifest, assets = reused
                elif (Path(file["filename"]).suffix.lower() in LOCAL_SHEETS and not visual_job) or manifest.get("kind") in {
                    "image",
                    "text",
                }:
                    with tempfile.TemporaryDirectory(prefix="ailab-preview-") as tmp:
                        source = Path(tmp) / ("input" + Path(file["filename"]).suffix.lower())
                        download(platform, file_id, source, settings.max_upload_bytes, job="preview")
                        if manifest["kind"] == "text":
                            info = execute(
                                "extract",
                                source,
                                Path(tmp) / "text",
                                filename=file["filename"],
                                content_type=file.get("content_type") or "",
                            )
                            data = info.get("text", "").encode("utf-8")
                            key = f"derived/{file_id}/preview/text.txt"
                            storage.put_bytes(key, data, "text/plain; charset=utf-8")
                            assets["text.txt"] = {
                                "key": key,
                                "content_type": "text/plain; charset=utf-8",
                                "sha256": hashlib.sha256(data).hexdigest(),
                            }
                            manifest = text_manifest(file["filename"], {"extract": info})
                        elif manifest["kind"] == "image":
                            derivatives = _images(file_id, source, file, storage, Path(tmp) / "images")
                            if not derivatives.get("display_key"):
                                raise ValueError("image_unavailable")
                            manifest = {"status": "ready", "kind": "image", "image": "display.webp"}
                        else:
                            execute("sheets", source, Path(tmp) / "sheets", timeout=120)
                            data = (Path(tmp) / "sheets" / "sheets.json").read_bytes()
                            key = f"derived/{file_id}/preview/sheets.json"
                            storage.put_bytes(key, data, "application/json")
                            assets["sheets.json"] = {
                                "key": key,
                                "content_type": "application/json",
                                "sha256": hashlib.sha256(data).hexdigest(),
                            }
                            manifest = {"status": "ready", "kind": "sheets", "data": "sheets.json"}
                else:
                    result = platform.render_preview(file_id, job_id)
                    manifest, assets = result["preview"], result["assets"]
                    render_lease_id = result.get("render_lease_id")
                    render_generation = result.get("render_generation")
            except Exception as exc:
                if isinstance(exc, ClientError) and exc.status in {429, 503}:
                    return {"skipped": "busy"}
                reason = str(exc) if isinstance(exc, ValueError | IsolationError) else "render_failed"
                manifest = {"status": "failed", "kind": manifest.get("kind"), "reason": reason[:200]}
            published = False
            try:
                publish_started = time.monotonic()
                platform.complete_preview(file_id, job_id, manifest, assets)
                published = True
                log.info("preview phase=publish seconds=%.3f", time.monotonic() - publish_started)
            except ClientError as exc:
                if exc.status == 404:  # deletion raced rendering: remove every derived object written after deletion
                    for obj in storage.list_prefix(f"derived/{file_id}/"):
                        storage.delete(obj.key)
                    return {"skipped": "deleted"}
                if exc.status == 409:
                    return {"skipped": "superseded"}
                raise
            finally:
                if render_lease_id and render_generation:
                    try:
                        platform.finish_render(render_lease_id, job_id, "preview", render_generation, published)
                    except Exception:
                        log.warning("render completion failed; lease expiry will release it", exc_info=True)
            return manifest
    finally:
        queue.release_lock(f"ailab:preview:{file_id}", token)
        log.info("preview completed total_seconds=%.3f slot_wait_seconds=%.3f", time.monotonic() - started, slot_seconds)
