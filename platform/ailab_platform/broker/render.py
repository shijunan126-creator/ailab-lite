"""On-demand library rendering using LeaseManager's public lifecycle and I/O API."""

from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import replace
from pathlib import Path

from ..config import settings
from ..contracts.file_preview import PAGES, SANDBOX_SHEETS
from ..contracts.sandbox import SandboxSpec
from ..domain.admission import LeasePrincipal
from .service import LeaseError, LeaseManager

SCRIPT = Path(__file__).resolve().parents[1] / "sandbox_tools" / "render_preview.py"


class LibraryRenderer:
    """Renders one library file in a short-lived sandbox of the reserved ``render`` class.

    A library file belongs to no run, so the lease is bound to the preview job instead: the API lets the Broker
    read the file only while that job is the file's pending one.
    """

    def __init__(self, manager: LeaseManager):
        self.manager = manager

    def _spec(self) -> SandboxSpec:
        ttl = settings.sandbox_render_max_ttl_seconds
        return replace(
            self.manager.default_spec(ttl_seconds=ttl), profile="render", memory=settings.sandbox_render_memory_bytes, max_lifetime=ttl
        )

    def render(self, file_id: str, job_id: str) -> dict:
        started = time.monotonic()
        platform = self.manager.platform
        file = platform.get_file(file_id, job="preview", preview_job=job_id)
        meta = file.get("meta") or {}
        if file.get("status") != "ready" or meta.get("preview_job_id") != job_id or (meta.get("preview") or {}).get("status") != "pending":
            raise LeaseError("preview job is not pending for this file")
        suffix = Path(file["filename"]).suffix.lower()
        pdf_mime = file.get("content_type", "").split(";")[0] == "application/pdf"
        if suffix not in PAGES | SANDBOX_SHEETS and not pdf_mime:
            raise LeaseError("unsupported sandbox preview type")
        source = "/workspace/inputs/input" + suffix

        def prepare(lease: dict) -> None:
            self.manager.inject_file(lease, file_id, source, job="preview", preview_job=job_id)
            self.manager.put_input(lease, "/workspace/.ailab/render_preview.py", SCRIPT.read_bytes())

        principal = LeasePrincipal(user_id="system:preview", run_id=f"preview:{job_id}", system=True)
        handle = self.manager.acquire(self._spec(), principal, wait_seconds=30, prepare=prepare)
        lease_id = handle.lease_id
        try:
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
                key = f"derived/{file_id}/preview/{name}"
                size, digest = self.manager.store_file(
                    self.manager.get(lease_id),
                    f"/workspace/.ailab/preview/{name}",
                    key,
                    mime,
                    min(settings.max_sandbox_read_bytes, 32 * 1024 * 1024 - total),
                )
                total += size
                assets[name] = {"key": key, "content_type": mime, "sha256": digest}
            return {"preview": preview, "assets": assets}
        finally:
            self.manager.release(handle, reason="preview_complete")
