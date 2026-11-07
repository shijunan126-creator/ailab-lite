"""Prepare selected pixels in the existing Work lease and call the existing platform OCR service."""

import json
import logging
import shlex
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..clients import ClientError
from ..contracts.workspace_ocr import PreparedImage, WorkspaceOcr
from ..paths import normalize_path

log = logging.getLogger("ailab.broker.workspace_ocr")
HELPER = Path(__file__).resolve().parents[1] / "sandbox_tools" / "workspace_ocr_image.py"
LIMITER = HELPER.with_name("office_exec.py")


def content(result, body):
    if result["status"] != "ready" or not result.get("content"):
        raise ValueError((result.get("result") or {}).get("reason") or "workspace_ocr_failed")
    value = result["content"]
    binding = value.get("source") or {}
    expected_region = body.region.model_dump(mode="json") if body.region else None
    if binding.get("path") != normalize_path(body.path) or binding.get("region") != expected_region:
        raise ValueError("workspace_ocr_scope_changed")
    return {"ok": True, **value}


def recognize(manager, lease_id, body: WorkspaceOcr):
    lease = manager.get(lease_id)
    run_id = lease["run_id"]
    path = normalize_path(body.path)
    if path.startswith("/workspace/.ailab/"):
        raise ValueError("invalid_workspace_path")
    platform = manager.platform
    if body.operation_id:
        return content(platform.workspace_ocr_status(run_id, body.operation_id, body.cursor), body)
    budget = platform.workspace_ocr_budget(run_id)
    deadline = datetime.fromisoformat(budget["deadline"]).timestamp()
    if deadline <= time.time():
        raise ValueError("document_run_deadline")
    directory = "/workspace/.ailab/ocr-" + uuid4().hex
    operation, completed = None, False
    manager._operation_started(lease_id)
    try:
        manager.register_scratch(lease_id, directory, deadline)
        manager.put_file(lease_id, directory + "/prepare.py", HELPER.read_bytes())
        manager.put_file(lease_id, directory + "/image_regions.py", (HELPER.parent.parent / "image_regions.py").read_bytes())
        manager.put_file(lease_id, directory + "/limit.py", LIMITER.read_bytes())
        remaining = min(30, max(1, int(deadline - time.time())))
        region = body.region.model_dump(mode="json") if body.region else None
        arguments = " ".join(shlex.quote(arg) for arg in [directory + "/prepare.py", path, directory, json.dumps(region)])
        result = manager.exec(
            lease_id, f"python {shlex.quote(directory + '/limit.py')} {remaining} {192 * 1024**2} python {arguments}", remaining + 2
        )
        if result.exit_code:
            reason = (
                "image_memory_limit"
                if result.exit_code == 137
                else "image_preparation_timeout"
                if result.exit_code == 124
                else "image_preparation_failed"
            )
            for line in result.stderr.splitlines()[-2:]:
                try:
                    error = json.loads(line).get("error")
                except (ValueError, AttributeError):
                    continue
                if error in {
                    "image_input_limit",
                    "image_pixel_limit",
                    "image_size_limit",
                    "invalid_region",
                    "workspace_source_changed",
                    "invalid_workspace_path",
                }:
                    reason = error
            raise ValueError(reason)
        with manager.transfer_file() as metadata:
            manager._download(manager.get(lease_id), directory + "/image.json", metadata, 8192)
            prepared = PreparedImage(path=path, region=region, **json.loads(metadata.read_text()))
        operation = platform.workspace_ocr_prepare(run_id, prepared.model_dump(mode="json"))
        with manager.transfer_file() as image:
            while True:
                manager.get(lease_id)  # a deleted/expired lease cannot continue polling or publish
                if time.time() >= deadline:
                    raise ValueError("document_run_deadline")
                if operation["status"] == "ready":
                    final = platform.workspace_ocr_status(run_id, operation["operation_id"])
                    completed = True
                    return content(final, body)
                if operation["status"] not in {"pending", "running"}:
                    raise ValueError((operation.get("result") or {}).get("reason") or "workspace_ocr_failed")
                if operation["status"] == "running":
                    manager._download(manager.get(lease_id), directory + "/image.jpg", image, 2 * 1024**2)
                    try:
                        final = platform.workspace_ocr_image(
                            run_id,
                            operation["operation_id"],
                            operation["claim_token"],
                            image,
                            datetime.fromisoformat(operation["execution_deadline"]).timestamp(),
                        )
                    except (ClientError, TimeoutError) as exc:
                        # A lost response is not permission to rerun inference.
                        # The durable receipt can settle while we only poll it.
                        if isinstance(exc, ClientError) and exc.status not in {409, 502, 503, 504}:
                            raise
                    else:
                        completed = True
                        return content(final, body)
                    operation = platform.workspace_ocr_status(run_id, operation["operation_id"])
                    if operation["status"] == "running":
                        # Do not dispatch a second body for the same claim.
                        while operation["status"] == "running" and time.time() < deadline:
                            time.sleep(min(0.25, max(0, deadline - time.time())))
                            manager.get(lease_id)
                            operation = platform.workspace_ocr_status(run_id, operation["operation_id"])
                    continue
                time.sleep(min(0.25, max(0, deadline - time.time())))
                operation = platform.workspace_ocr_status(run_id, operation["operation_id"])
    finally:
        try:
            if operation and not completed and operation.get("claim_token"):
                try:
                    platform.workspace_ocr_cancel(run_id, operation["operation_id"], operation["claim_token"])
                except Exception:
                    log.warning("workspace OCR cancellation pending run=%s", run_id)
            try:
                manager.clear_scratch(lease_id, directory)
            except Exception:
                # The lease record/provider metadata retain this exact owned
                # scratch path, deadline and bounded crash-compensation intent.
                log.warning("workspace OCR scratch cleanup pending lease=%s", lease_id)
        finally:
            manager._operation_finished(lease_id)
