"""Build the public viewer index from the bounded native-reader manifest."""

import hashlib
import json
import threading

from ..contracts.document_view import NATIVE_BYTES, NATIVE_UNITS, PARSER_REVISION

_reader_slot = threading.BoundedSemaphore(1)


def execute_reader(source, output, **options):
    from .isolate import IsolationError, execute

    if not _reader_slot.acquire(blocking=False):
        raise IsolationError("viewer_busy")
    try:
        return execute("viewer", source, output, **options)
    finally:
        _reader_slot.release()


def build(file_id, version, filename, size, parsed):
    kind, units = parsed["kind"], parsed["units"]
    native = size <= NATIVE_BYTES[kind] and units <= NATIVE_UNITS[kind]
    if kind == "xlsx":
        native = native and isinstance(parsed.get("cells"), int) and parsed["cells"] <= NATIVE_UNITS[kind]
    if kind == "docx":
        native = native and units <= len(parsed.get("blocks", []))
    result = {
        "status": "ready",
        "file_id": file_id,
        "version": version,
        "parser_revision": PARSER_REVISION,
        "kind": kind,
        "title": parsed.get("title") or filename,
        "bytes": size,
        "units": units,
        "outline": parsed.get("outline", [])[:2000],
        "native": native,
    }
    if kind == "docx":
        result["blocks"] = parsed.get("blocks", [])
    if not native:
        result["reason"] = "native_admission_limit"
    return result


def process_viewer(platform, file_id, *, job_id=None):
    from ..derived_writes import accounted_writes
    from ..storage import get_storage

    if job_id is None:
        return _process_viewer(platform, file_id, get_storage())
    file = platform.get_file(file_id, job="viewer")
    with accounted_writes(get_storage(), platform, file["sha256"], job_id) as writer:
        result = _process_viewer(platform, file_id, writer, job_id=job_id)
        if result.get("status") != "pending":
            writer.handoff()
        return result


def _process_viewer(platform, file_id, storage, *, job_id=None):
    from pathlib import Path

    from ..owned_temporary import directory as temporary_directory
    from .pipeline import download

    file = platform.get_file(file_id, job="viewer")
    cached_key = (file.get("meta") or {}).get("viewer_key") or f"derived/{file_id}/viewer.json"
    key = f"derived/{file_id}/viewer/{PARSER_REVISION}/{job_id}.json" if job_id else cached_key
    if storage.exists(cached_key) and storage.size(cached_key) <= 4 * 1024**2:
        cached = json.loads(storage.get_bytes(cached_key))
        if cached.get("version") == file["sha256"] and cached.get("parser_revision") == PARSER_REVISION:
            return cached
    kind = Path(file["filename"]).suffix.lower().removeprefix(".")
    if file["size"] > NATIVE_BYTES[kind]:
        result = {
            "status": "ready",
            "file_id": file_id,
            "version": file["sha256"],
            "parser_revision": PARSER_REVISION,
            "kind": kind,
            "title": file["filename"],
            "bytes": file["size"],
            "native": False,
            "reason": "native_admission_limit",
        }
        storage.put_bytes(key, json.dumps(result).encode(), "application/json")
        return result
    with temporary_directory(prefix="ailab-viewer-") as directory:
        source = Path(directory) / ("source" + Path(file["filename"]).suffix.lower())
        download(platform, file_id, source, 20 * 1024**2, job="viewer")
        return derive(file, source, storage, Path(directory) / "read", key=key)


def derive(file, source, storage, output, *, key=None):
    from ..config import settings
    from .isolate import IsolationError

    try:
        with source.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != file["sha256"]:
                raise IsolationError("document_version_changed")
        parsed = execute_reader(source, output, timeout=22, memory_mb=256, reader=str(settings.viewer_reader_path))
        result = build(file["id"], file["sha256"], file["filename"], file["size"], parsed)
    except IsolationError as exc:
        if str(exc) == "viewer_busy":
            return {"status": "pending", "reason": "viewer_busy"}
        result = {
            "status": "failed",
            "file_id": file["id"],
            "version": file["sha256"],
            "parser_revision": PARSER_REVISION,
            "native": False,
            "reason": "viewer_reader_unavailable",
        }
    storage.put_bytes(key or f"derived/{file['id']}/viewer.json", json.dumps(result, ensure_ascii=False).encode(), "application/json")
    return result


def check(platform, file_id, version, selections):
    from pathlib import Path

    from ..config import settings
    from ..owned_temporary import directory as temporary_directory
    from .pipeline import download

    file = platform.get_file(file_id, job="viewer")
    if file["sha256"] != version:
        raise ValueError("version changed")
    with temporary_directory(prefix="ailab-viewer-check-") as directory:
        source = Path(directory) / ("source" + Path(file["filename"]).suffix.lower())
        download(platform, file_id, source, 20 * 1024**2, job="viewer")
        with source.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != version:
                raise ValueError("document version changed")
        result = execute_reader(
            source,
            Path(directory) / "read",
            timeout=22,
            memory_mb=256,
            reader=str(settings.viewer_reader_path),
            selections=selections,
        )
        return {"version": version, "selections": result["selections"]}
