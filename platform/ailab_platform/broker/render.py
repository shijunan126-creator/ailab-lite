"""On-demand library rendering using LeaseManager's public lifecycle and I/O API."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import threading
import time
import uuid
from pathlib import Path

from ..contracts.file_preview import PAGES, SANDBOX_SHEETS
from .service import LeaseError, LeaseManager

SCRIPT = Path(__file__).resolve().parents[1] / "sandbox_tools" / "render_preview.py"


class RenderContexts:
    """File jobs have no conversation Run. Only verified jobs obtain a short-lived injection context."""

    def __init__(self, platform):
        self.platform = platform
        self.contexts: dict[str, str] = {}
        self.lock = threading.Lock()

    def get_run(self, run_id: str) -> dict:
        with self.lock:
            file_id = self.contexts.get(run_id)
        if file_id is not None:
            return {"id": run_id, "input_file_ids": [file_id]}
        return self.platform.get_run(run_id)

    def append_event(self, run_id, *args, **kwargs):
        with self.lock:
            if run_id in self.contexts:
                return {}
        return self.platform.append_event(run_id, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.platform, name)


class LibraryRenderer:
    def __init__(self, manager: LeaseManager):
        self.manager = manager
        self.contexts = RenderContexts(manager.platform)
        manager.platform = self.contexts

    def render(self, file_id: str, job_id: str) -> dict:
        started = time.monotonic()
        file = self.contexts.platform.get_file(file_id)
        meta = file.get("meta") or {}
        if file.get("status") != "ready" or meta.get("preview_job_id") != job_id or (meta.get("preview") or {}).get("status") != "pending":
            raise LeaseError("preview job is not pending for this file")
        suffix = Path(file["filename"]).suffix.lower()
        pdf_mime = file.get("content_type", "").split(";")[0] == "application/pdf"
        if suffix not in PAGES | SANDBOX_SHEETS and not pdf_mime:
            raise LeaseError("unsupported sandbox preview type")
        context = "preview_" + uuid.uuid4().hex
        with self.contexts.lock:
            self.contexts.contexts[context] = file_id
        lease = None
        try:
            lease = self.manager.create(context, ttl_seconds=180)
            lease_id = lease["lease_id"]
            self.manager.put_file(lease_id, "/workspace/.ailab/render_preview.py", SCRIPT.read_bytes())
            source = next(item["path"] for item in lease["inputs"] if item["file_id"] == file_id)
            if pdf_mime and suffix != ".pdf":
                self.manager.exec(lease_id, f"cp {shlex.quote(source)} /workspace/.ailab/input.pdf", 5)
                source = "/workspace/.ailab/input.pdf"
            if time.monotonic() - started >= 120:
                raise LeaseError("render_timeout")
            remaining = max(1, int(120 - (time.monotonic() - started)))
            result = self.manager.exec(
                lease_id,
                f"timeout -k 2 {remaining}s python /workspace/.ailab/render_preview.py {shlex.quote(source)} /workspace/.ailab/preview",
                remaining,
            )
            if time.monotonic() - started >= 120 or result.exit_code != 0:
                raise LeaseError("render_timeout" if time.monotonic() - started >= 120 else "render_failed")
            try:
                manifest = json.loads((result.stdout.strip().splitlines() or ["{}"])[-1])
            except (ValueError, TypeError) as exc:
                raise LeaseError("invalid_render_output") from exc
            if not isinstance(manifest, dict):
                raise LeaseError("invalid_render_output")
            if manifest.get("error"):
                raise LeaseError("render_failed")
            kind = manifest.get("kind")
            if kind == "pages":
                names = manifest.get("pages") or []
                if not names or len(names) > 40 or any(not re.fullmatch(r"p-\d+\.jpg", n) for n in names):
                    raise LeaseError("invalid_render_output")
                preview = {"status": "ready", "kind": "pages", "pages": names, "page_count": manifest.get("page_count")}
                mime = "image/jpeg"
            elif kind == "sheets" and manifest.get("data") == "sheets.json":
                names, mime = ["sheets.json"], "application/json"
                preview = {"status": "ready", "kind": "sheets", "data": "sheets.json"}
            else:
                raise LeaseError("invalid_render_output")
            assets, total = {}, 0
            # Manager enforces a per-object read cap; additionally bound the complete preview.
            for name in names:
                if time.monotonic() - started >= 120:
                    raise LeaseError("render_timeout")
                data = self.manager.get_file(lease_id, f"/workspace/.ailab/preview/{name}")
                total += len(data)
                if total > 32 * 1024 * 1024:
                    raise LeaseError("preview_output_limit")
                key = f"derived/{file_id}/preview/{name}"
                self.manager.storage.put_bytes(key, data, mime)
                assets[name] = {"key": key, "content_type": mime, "sha256": hashlib.sha256(data).hexdigest()}
            return {"preview": preview, "assets": assets}
        finally:
            try:
                if lease:
                    self.manager.release(lease["lease_id"], reason="preview_complete")
            finally:
                with self.contexts.lock:
                    self.contexts.contexts.pop(context, None)
