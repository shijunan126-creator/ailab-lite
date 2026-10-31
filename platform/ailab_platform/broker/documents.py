"""Trusted Office reader jobs in short-lived system sandboxes, never a user's lease."""

import shlex
import time
from dataclasses import replace
from pathlib import PurePath

from ..config import settings
from ..domain.admission import LeasePrincipal
from .service import LeaseError

KINDS = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx"}


class DocumentReader:
    def __init__(self, manager):
        self.manager = manager

    def read(self, file_id, job_id):
        with self.manager.render_activity():
            return self._read(file_id, job_id)

    def _read(self, file_id, job_id):
        total_started = time.monotonic()
        phases = {}
        file = self.manager.platform.get_file(file_id, job="document", preview_job=job_id)
        kind = KINDS.get(PurePath(file["filename"]).suffix.lower())
        if not kind or file.get("size", 0) > settings.max_parse_bytes:
            raise LeaseError("document_input_limit")
        ttl = settings.sandbox_render_max_ttl_seconds
        spec = replace(
            self.manager.default_spec(ttl_seconds=ttl),
            profile="render",
            image=settings.document_sandbox_image,
            memory=min(settings.sandbox_render_memory_bytes, (settings.office_memory_mb + 64) * 1024**2),
            max_lifetime=ttl,
        )
        source = "/workspace/inputs/document." + kind

        def prepare(lease):
            injection_started = time.monotonic()
            self.manager.inject_file(lease, file_id, source, job="document", preview_job=job_id, max_bytes=settings.max_parse_bytes)
            phases["inject_seconds"] = time.monotonic() - injection_started

        acquire_started = time.monotonic()
        handle = self.manager.acquire(
            spec, LeasePrincipal(user_id="system:document", run_id="document:" + job_id, system=True), wait_seconds=30, prepare=prepare
        )
        phases["acquire_seconds"] = time.monotonic() - acquire_started
        started = time.monotonic()
        try:
            out = "/workspace/.ailab/document"
            command = (
                f"mkdir -p {out} && timeout -k 2 {int(settings.office_timeout_seconds)}s "
                f"node --max-old-space-size={max(32, settings.office_memory_mb - 96)} /opt/ailab-office/runner.mjs "
                f"{shlex.quote(source)} {out} {kind} && COPYFILE_DISABLE=1 tar -cf /workspace/.ailab/document.tar -C {out} ."
            )
            result = self.manager.exec(handle.lease_id, command, int(settings.office_timeout_seconds) + 10)
            phases["exec_seconds"] = time.monotonic() - started
            if result.exit_code:
                return {"error": "document_runtime_unavailable" if result.exit_code == 127 else "document_parse_failed"}
            key = f"derived/{file_id}/office-staging/{job_id}.tar"
            export_started = time.monotonic()
            size, digest = self.manager.store_file(
                self.manager.get(handle.lease_id), "/workspace/.ailab/document.tar", key, "application/x-tar", 48 * 1024**2
            )
            phases["export_seconds"] = time.monotonic() - export_started
            return {"archive_key": key, "bytes": size, "sha256": digest, "sandbox_seconds": time.monotonic() - started, "phases": phases}
        finally:
            release_started = time.monotonic()
            self.manager.release(handle, reason="document_complete")
            phases["release_seconds"] = time.monotonic() - release_started
            phases["total_seconds"] = time.monotonic() - total_started
