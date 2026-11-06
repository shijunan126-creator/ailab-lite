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
        file = self.manager.platform.get_file(file_id, job="document", preview_job=job_id)
        owner = file.get("render_owner_id")
        if not isinstance(owner, str) or not owner:
            raise LeaseError("document job has no authorized owner")
        with self.manager.render_activity(owner, priority=2):
            return self._read(file_id, job_id, owner)

    def _read(self, file_id, job_id, scheduled_owner):
        total_started = time.monotonic()
        phases = {}
        file = self.manager.platform.get_file(file_id, job="document", preview_job=job_id)
        kind = KINDS.get(PurePath(file["filename"]).suffix.lower())
        profile = (file.get("meta") or {}).get("document_profile", "legacy")
        if profile not in {"legacy", "chat", "chat-full", "scope"}:
            raise LeaseError("document_profile_invalid")
        if profile != "legacy":
            kind = (file.get("meta") or {}).get("document_kind") or kind
            if kind not in {"pdf", "docx", "pptx", "xlsx"}:
                raise LeaseError("document_format_unsupported")
        if not kind or file.get("size", 0) > settings.max_parse_bytes:
            raise LeaseError("document_input_limit")
        owner = file.get("render_owner_id")
        if owner != scheduled_owner:
            raise LeaseError("document job owner changed while waiting")
        spec = LibraryRenderer(self.manager)._spec()
        source = "/workspace/inputs/document." + kind

        def remaining_seconds():
            from datetime import datetime

            deadline = (file.get("meta") or {}).get("document_deadline") if profile == "scope" else None
            remaining = datetime.fromisoformat(deadline).timestamp() - time.time() if deadline else settings.office_timeout_seconds + 10
            if remaining <= 0:
                raise LeaseError("document_operation_deadline")
            return remaining

        def prepare(lease):
            remaining_seconds()
            injection_started = time.monotonic()
            self.manager.inject_file(lease, file_id, source, job="document", preview_job=job_id, max_bytes=settings.max_parse_bytes)
            script = Path(__file__).resolve().parents[1] / "sandbox_tools" / "office_exec.py"
            self.manager.put_input(lease, "/workspace/.ailab/office_exec.py", script.read_bytes())
            if profile == "scope":
                import json

                self.manager.put_input(
                    lease, "/workspace/.ailab/document-request.json", json.dumps((file.get("meta") or {}).get("document_selector")).encode()
                )
            phases["inject_seconds"] = time.monotonic() - injection_started

        acquire_started = time.monotonic()
        handle, lease_source = self.manager.acquire_render(
            spec, LeasePrincipal(user_id=owner, run_id="document:" + job_id, system=True), prepare
        )
        phases["acquire_seconds"] = time.monotonic() - acquire_started
        started = time.monotonic()
        returned = False
        transfer_grant = None
        try:
            out = "/workspace/.ailab/document"
            execution_seconds = min(settings.office_timeout_seconds, max(1, int(remaining_seconds()) - 5))
            command = (
                f"mkdir -p {out} && python /workspace/.ailab/office_exec.py {execution_seconds} "
                f"{(settings.office_memory_mb + 64) * 1024**2} "
                f"node --max-old-space-size={max(32, settings.office_memory_mb - 96)} /opt/ailab-office/runner.mjs "
                f"{shlex.quote(source)} {out} {kind} {profile} "
                f"{'/workspace/.ailab/document-request.json' if profile == 'scope' else ''} && COPYFILE_DISABLE=1 tar -cf /workspace/.ailab/document.tar -C {out} ."
            )
            result = self.manager.exec(handle.lease_id, command, min(int(remaining_seconds()), int(execution_seconds) + 5))
            phases["exec_seconds"] = time.monotonic() - started
            if result.exit_code:
                import json

                reason = "document_runtime_unavailable" if result.exit_code == 127 else "document_parse_failed"
                for line in result.stderr.splitlines()[-4:]:
                    try:
                        reported = json.loads(line).get("error")
                    except (ValueError, AttributeError):
                        continue
                    if reported in {"document_text_limit", "document_cells_limit", "document_structure_limit", "output_limit"}:
                        reason = reported
                return {"error": reason}
            key = f"derived/{file_id}/office-staging/{job_id}.tar"
            export_started = time.monotonic()
            transfer_grant = None
            transfer_limit = 8 * 1024**2 if profile != "legacy" else 48 * 1024**2
            if profile != "legacy":
                native = ((file.get("meta") or {}).get("representations") or {}).get("native_text") or {}
                transfer_grant = self.manager.platform.reserve_derived(
                    key=key,
                    version=file["sha256"],
                    max_bytes=transfer_limit,
                    kind="transfer",
                    job_id=(file.get("meta") or {}).get("document_operation_id") if profile == "scope" else native.get("job_id"),
                )
            size, digest = self.manager.store_file(
                self.manager.get(handle.lease_id), "/workspace/.ailab/document.tar", key, "application/x-tar", transfer_limit
            )
            if transfer_grant:
                self.manager.platform.finish_derived(key, transfer_grant["token"])
            phases["export_seconds"] = time.monotonic() - export_started
            generation = self.manager.get(handle)["render_generation"]
            returned = True
            return {
                "archive_key": key,
                **({"transfer_receipt": {"key": key, "token": transfer_grant["token"]}} if transfer_grant else {}),
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
                try:
                    if transfer_grant:
                        try:
                            self.manager.storage.delete(transfer_grant["key"])
                            self.manager.platform.discard_derived_batch([{"key": transfer_grant["key"], "token": transfer_grant["token"]}])
                        except Exception:
                            self.manager.platform.abandon_derived(transfer_grant["key"], transfer_grant["token"])
                finally:
                    self.manager.release(handle, reason="document_failed")
            phases["release_seconds"] = time.monotonic() - release_started
            phases["total_seconds"] = time.monotonic() - total_started
