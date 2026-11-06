"""Two-object native generations: bounded transfer, batch accounting, atomic API publication."""

import hashlib
import json
import logging
import tarfile
import tempfile
from pathlib import Path

from ..native_text import CANONICAL_TEXT_BYTES, PARSER_NATIVE, read_unit
from .isolate import IsolationError

log = logging.getLogger("ailab.worker.native")
MAX_ARCHIVE = 8 * 1024**2
MAX_MANIFEST = 4 * 1024**2


def unpack(storage, archive_key, output, expected):
    archive = output / "transfer.tar"
    digest, size = hashlib.sha256(), 0
    with archive.open("wb") as target, storage.open_stream(archive_key) as chunks:
        for chunk in chunks:
            size += len(chunk)
            if size > MAX_ARCHIVE:
                raise IsolationError("native_transfer_limit")
            digest.update(chunk)
            target.write(chunk)
    if size != expected["bytes"] or digest.hexdigest() != expected["sha256"]:
        raise IsolationError("native_transfer_mismatch")
    seen = set()
    with tarfile.open(archive, mode="r|") as bundle:
        for member in bundle:
            name = member.name.removeprefix("./")
            if member.isdir() and name in {"", "."}:
                continue
            if name not in {"manifest.json", "text.txt"} or not member.isfile() or name in seen:
                raise IsolationError("invalid_native_bundle")
            if member.size > (MAX_MANIFEST if name == "manifest.json" else CANONICAL_TEXT_BYTES):
                raise IsolationError("native_output_limit")
            seen.add(name)
            source = bundle.extractfile(member)
            with source, (output / name).open("wb") as target:
                while chunk := source.read(65536):
                    target.write(chunk)
    if seen != {"manifest.json", "text.txt"}:
        raise IsolationError("invalid_native_bundle")
    manifest = json.loads((output / "manifest.json").read_text())
    if manifest.get("parser") != PARSER_NATIVE or manifest.get("version") != 2 or manifest.get("status") not in {"ready", "partial"}:
        raise IsolationError("invalid_native_manifest")
    if manifest.get("chars", 200001) > 200000 or len(manifest.get("units", [])) > 10000 or (manifest.get("cells") or 0) > 200000:
        raise IsolationError("native_structure_limit")

    # Verify canonical ranges once before uploading. This local adapter only
    # consumes trusted filenames from the fixed two-file transport contract.
    class Bundle:
        from contextlib import contextmanager

        @contextmanager
        def open_stream(self, key):
            with (output / key).open("rb") as stream:
                yield iter(lambda: stream.read(65536), b"")

    cache = {}
    for ordinal, unit in enumerate(manifest["units"]):
        if unit["ordinal"] != ordinal or unit["name"] != "text.txt":
            raise IsolationError("invalid_native_manifest")
        read_unit(Bundle(), "text.txt", unit, cache)
    return manifest


def process_native(platform, file_id, job_id, storage=None, *, scoped=False):
    from ..clients import ClientError
    from ..storage import get_storage

    storage = storage or get_storage()
    start = platform.start_native_scope(file_id, job_id) if scoped else platform.start_native(file_id, job_id)
    if start.get("status") in {"ready", "failed", "canceled"}:
        return True
    if start.get("status") == "pending":
        return False
    complete = platform.complete_native_scope if scoped else platform.complete_native
    from .isolate import execute
    from .pipeline import download

    result, archive_key = {}, None
    success, receipts = False, []
    try:
        with tempfile.TemporaryDirectory(prefix="ailab-native-") as temporary:
            output = Path(temporary)
            if start["kind"] in {"pdf", "docx", "pptx", "xlsx"}:
                result = platform.read_document(file_id, start["job_id"])
                archive_key = result.get("archive_key")
                if result.get("error"):
                    complete(file_id, job_id, reason=result["error"])
                    return
                if archive_key != f"derived/{file_id}/office-staging/{start['job_id']}.tar":
                    raise IsolationError("invalid_native_transfer")
                manifest = unpack(storage, archive_key, output, result)
            else:
                source = output / "source"
                download(platform, file_id, source, 20 * 1024**2, job="viewer")
                with source.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != start["version"]:
                        raise IsolationError("file_integrity_mismatch")
                output = output / "native"
                remaining = None
                if start.get("execution_deadline"):
                    from datetime import datetime
                    import time

                    remaining = min(120, datetime.fromisoformat(start["execution_deadline"]).timestamp() - time.time())
                    if remaining <= 0:
                        raise IsolationError("document_operation_deadline")
                manifest = execute(
                    "native_text",
                    source,
                    output,
                    ownership=(lambda: platform.native_owned(file_id, start["job_id"])) if start.get("execution_deadline") else None,
                    **({"timeout": remaining} if remaining else {}),
                )
            prefix = f"derived/{file_id}/office/{job_id}-{start['job_id']}/"
            items = [
                {
                    "key": prefix + name,
                    "version": start["version"],
                    "max_bytes": (output / name).stat().st_size,
                    "kind": "native",
                    "job_id": job_id,
                }
                for name in ["manifest.json", "text.txt"]
            ]
            grants = platform.reserve_derived_batch(items)["items"]
            receipts = [{"key": grant["key"], "token": grant["token"]} for grant in grants]
            for name, grant in zip(["manifest.json", "text.txt"], grants, strict=True):
                storage.put_file(grant["key"], output / name, "application/json" if name.endswith("json") else "text/plain; charset=utf-8")
            platform.finish_derived_batch(receipts)
            published = complete(file_id, job_id, manifest_key=prefix + "manifest.json", receipts=receipts)
            success = published.get("status") == "ready"
            log.info("native generation published kind=%s chars=%d units=%d", manifest["kind"], manifest["chars"], manifest["total_units"])
    except (ValueError, IsolationError) as exc:
        complete(file_id, job_id, reason=str(exc)[:80])
    except ClientError:
        raise  # durable retry keeps the existing source/job identity
    finally:
        if receipts and not success:
            try:
                abandoned = platform.abandon_derived_batch(receipts)
                # A lost successful publication response must never cause us to
                # delete ready assets. API atomically grants deletion only for
                # unpublished generations, fencing any concurrent publication.
                for receipt in abandoned.get("deletable", []):
                    storage.delete(receipt["key"])
                    platform.discard_derived_batch([receipt])
            except Exception:
                log.warning("native transfer cleanup awaits durable lease expiry", exc_info=True)
        try:
            if archive_key == f"derived/{file_id}/office-staging/{start['job_id']}.tar":
                try:
                    storage.delete(archive_key)
                    if result.get("transfer_receipt"):
                        platform.discard_derived_batch([result["transfer_receipt"]])
                except Exception:
                    if result.get("transfer_receipt"):
                        platform.abandon_derived_batch([result["transfer_receipt"]])
                    log.warning("native archive cleanup awaits durable compensation", exc_info=True)
        finally:
            if result.get("render_lease_id") and result.get("render_generation"):
                try:
                    platform.finish_render(result["render_lease_id"], start["job_id"], "document", result["render_generation"], success)
                except Exception:
                    log.warning("native render release awaits lease expiry", exc_info=True)
