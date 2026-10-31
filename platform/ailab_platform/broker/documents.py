"""Trusted Office reader jobs in short-lived system sandboxes, never a user's lease."""

import shlex
import time
from pathlib import Path, PurePath

from ..config import settings
from ..domain.admission import LeasePrincipal
from .render import LibraryRenderer
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
        owner = file.get("render_owner_id")
        if not isinstance(owner, str) or not owner:
            raise LeaseError("document job has no authorized owner")
        spec = LibraryRenderer(self.manager)._spec()
        source = "/workspace/inputs/document." + kind

        def prepare(lease):
            injection_started = time.monotonic()
            self.manager.inject_file(lease, file_id, source, job="document", preview_job=job_id, max_bytes=settings.max_parse_bytes)
            script = Path(__file__).resolve().parents[1] / "sandbox_tools" / "office_exec.py"
            self.manager.put_input(lease, "/workspace/.ailab/office_exec.py", script.read_bytes())
            phases["inject_seconds"] = time.monotonic() - injection_started

        acquire_started = time.monotonic()
        handle, lease_source = self.manager.acquire_render(
            spec, LeasePrincipal(user_id=owner, run_id="document:" + job_id, system=True), prepare
        )
        phases["acquire_seconds"] = time.monotonic() - acquire_started
        started = time.monotonic()
        returned = False
        try:
            out = "/workspace/.ailab/document"
            command = (
                f"mkdir -p {out} && python /workspace/.ailab/office_exec.py {settings.office_timeout_seconds} "
                f"{(settings.office_memory_mb + 64) * 1024**2} "
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
            generation = self.manager.get(handle)["render_generation"]
            returned = True
            return {
                "archive_key": key,
                "bytes": size,
                "sha256": digest,
                "sandbox_seconds": time.monotonic() - started,
                "phases": phases,
                "lease_source": lease_source,
                "render_lease_id": handle.lease_id,
                "render_generation": generation,
            }
        finally:
            release_started = time.monotonic()
            if not returned:
                self.manager.release(handle, reason="document_failed")
            phases["release_seconds"] = time.monotonic() - release_started
            phases["total_seconds"] = time.monotonic() - total_started
