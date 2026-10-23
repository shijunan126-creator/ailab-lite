"""Preview jobs are independent of upload scanning and never alter file readiness."""

from __future__ import annotations

import hashlib
import tempfile
import threading
from pathlib import Path

from ..clients import ClientError, PlatformClient
from ..config import settings
from ..contracts.file_preview import LOCAL_SHEETS
from ..queue import get_queue
from ..storage import get_storage
from .isolate import IsolationError, execute
from .pipeline import _images, download

_render_slot = threading.Lock()


def process_preview(file_id: str, job_id: str, platform: PlatformClient | None = None) -> dict:
    if platform is None:
        with PlatformClient() as owned:
            return process_preview(file_id, job_id, owned)
    queue = get_queue()
    token = queue.acquire_lock(f"ailab:preview:{file_id}", 300)
    if token is None:
        return {"skipped": "in_flight"}
    try:
        with _render_slot:
            try:
                file = platform.get_file(file_id)
            except ClientError as exc:
                if exc.status == 404:
                    return {"skipped": "deleted"}
                raise
            meta = file.get("meta") or {}
            if meta.get("preview_job_id") != job_id:
                return {"skipped": "superseded"}
            manifest = meta.get("preview") or {}
            if file.get("status") != "ready" or manifest.get("status") != "pending":
                return {"skipped": "already_complete"}
            storage = get_storage()
            assets = {}
            try:
                if Path(file["filename"]).suffix.lower() in LOCAL_SHEETS or manifest.get("kind") == "image":
                    with tempfile.TemporaryDirectory(prefix="ailab-preview-") as tmp:
                        source = Path(tmp) / ("input" + Path(file["filename"]).suffix.lower())
                        download(platform, file_id, source, settings.max_upload_bytes)
                        if manifest["kind"] == "image":
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
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ValueError | IsolationError) else "render_failed"
                manifest = {"status": "failed", "kind": manifest.get("kind"), "reason": reason[:200]}
            try:
                platform.complete_preview(file_id, job_id, manifest, assets)
            except ClientError as exc:
                if exc.status == 404:  # deletion raced rendering: remove every derived object written after deletion
                    for obj in storage.list_prefix(f"derived/{file_id}/"):
                        storage.delete(obj.key)
                    return {"skipped": "deleted"}
                if exc.status == 409:
                    return {"skipped": "superseded"}
                raise
            return manifest
    finally:
        queue.release_lock(f"ailab:preview:{file_id}", token)
