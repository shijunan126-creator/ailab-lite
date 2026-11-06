"""Preview jobs are independent of upload scanning and never alter file readiness."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ..clients import ClientError, PlatformClient
from ..config import settings
from ..contracts.file_preview import LOCAL_SHEETS, cached_page_preview, text_manifest
from ..contracts.file_types import legacy_scanned_pending
from ..owned_temporary import directory as temporary_directory
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
            scanned = legacy_scanned_pending(file.get("status"), meta)
            if not (file.get("status") == "ready" or scanned) or manifest.get("status") != "pending":
                return {"skipped": "already_complete"}
            ownership = None
            if hasattr(platform, "start_file_work"):
                ownership = platform.start_file_work(job_id)
                if ownership["status"] == "pending":
                    return {"skipped": "busy"}
                if ownership["status"] in {"ready", "failed", "canceled"}:
                    return {"skipped": ownership["status"]}
            execution_deadline = ownership.get("execution_deadline") if ownership else None

            def check_owned():
                if execution_deadline:
                    from datetime import datetime

                    if time.time() >= datetime.fromisoformat(execution_deadline).timestamp():
                        raise IsolationError("document_operation_deadline")
                    platform.file_work_owned(job_id)

            from ..derived_writes import AccountedWrites

            raw_storage = get_storage()
            storage = (
                AccountedWrites(raw_storage, platform, file["sha256"], job_id) if hasattr(platform, "reserve_derived") else raw_storage
            )
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
                    with temporary_directory(prefix="ailab-preview-") as tmp:
                        source = Path(tmp) / ("input" + Path(file["filename"]).suffix.lower())
                        download(platform, file_id, source, settings.max_upload_bytes, job="preview")
                        if manifest["kind"] == "text":
                            info = execute(
                                "extract",
                                source,
                                Path(tmp) / "text",
                                filename=file["filename"],
                                content_type=file.get("content_type") or "",
                                ownership=check_owned,
                            )
                            data = info.get("text", "").encode("utf-8")
                            key = f"derived/{file_id}/preview/{job_id}/text.txt"
                            storage.put_bytes(key, data, "text/plain; charset=utf-8")
                            assets["text.txt"] = {
                                "key": key,
                                "content_type": "text/plain; charset=utf-8",
                                "sha256": hashlib.sha256(data).hexdigest(),
                            }
                            manifest = text_manifest(file["filename"], {"extract": info})
                        elif manifest["kind"] == "image":
                            derivatives = _images(
                                file_id, source, file, storage, Path(tmp) / "images", prefix=f"derived/{file_id}/preview/{job_id}/"
                            )
                            if not derivatives.get("display_key"):
                                raise ValueError("image_unavailable")
                            assets = {
                                name: {
                                    "key": derivatives[field],
                                    "content_type": "image/webp",
                                    "sha256": hashlib.sha256((Path(tmp) / "images" / name).read_bytes()).hexdigest(),
                                }
                                for name, field in (("display.webp", "display_key"), ("thumb.webp", "thumb_key"))
                            }
                            manifest = {"status": "ready", "kind": "image", "image": "display.webp"}
                        else:
                            execute("sheets", source, Path(tmp) / "sheets", timeout=120, ownership=check_owned)
                            data = (Path(tmp) / "sheets" / "sheets.json").read_bytes()
                            key = f"derived/{file_id}/preview/{job_id}/sheets.json"
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
                if exc.status == 404:
                    # Only this writer's unpublished grants are ours to clean;
                    # source deletion owns older/current generation invalidation.
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
        try:
            if "storage" in locals() and hasattr(storage, "cleanup"):
                storage.cleanup()
        except Exception:
            log.warning("preview write compensation remains in durable ledger", exc_info=True)
        finally:
            queue.release_lock(f"ailab:preview:{file_id}", token)
        log.info("preview completed total_seconds=%.3f slot_wait_seconds=%.3f", time.monotonic() - started, slot_seconds)
