"""Consume bounded output of a Broker system sandbox; no Node/parser on Worker."""

import hashlib
import json
import re
import tarfile

from .isolate import IsolationError

PARSER_VERSION = "zcode-3c5b227-ailab1"
KINDS = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx"}


def read(file_id, platform, storage, output, scan):
    job = platform.prepare_document(file_id, scan)
    result = platform.read_document(file_id, job["job_id"])
    if result.get("error"):
        raise IsolationError(result["error"])
    key = result["archive_key"]
    if key != f"derived/{file_id}/office-staging/{job['job_id']}.tar" or result["bytes"] > 48 * 1024**2:
        raise IsolationError("invalid_document_output")
    try:
        output.mkdir(parents=True, exist_ok=True)
        archive = output / "bundle.tar"
        size = 0
        digest = hashlib.sha256()
        with archive.open("wb") as target, storage.open_stream(key) as chunks:
            for chunk in chunks:
                size += len(chunk)
                if size > 48 * 1024**2:
                    raise IsolationError("output_limit")
                digest.update(chunk)
                target.write(chunk)
        if size != result["bytes"] or digest.hexdigest() != result["sha256"]:
            raise IsolationError("document_transfer_mismatch")
        # Treat even system-parser output as untrusted; only flat regular files are accepted.
        seen = set()
        total = 0
        with tarfile.open(archive, mode="r|") as bundle:
            for member in bundle:
                name = member.name.removeprefix("./")
                if member.isdir() and name in {"", "."}:
                    continue
                if (
                    not member.isfile()
                    or not re.fullmatch(r"(?:\d{6}\.txt|manifest\.json|text\.txt)", name)
                    or name in seen
                    or len(seen) > 100002
                ):
                    raise IsolationError("invalid_document_archive")
                total += member.size
                if total > 40 * 1024**2 or member.size > (
                    16 * 1024**2 if name == "text.txt" else 8 * 1024**2 if name == "manifest.json" else 100000
                ):
                    raise IsolationError("output_limit")
                seen.add(name)
                source = bundle.extractfile(member)
                with (output / name).open("wb") as target:
                    while chunk := source.read(65536):
                        target.write(chunk)
        manifest = json.loads((output / "manifest.json").read_text())
        if manifest.get("parser") != PARSER_VERSION or manifest.get("status") not in {"ready", "partial"}:
            raise IsolationError("invalid_document_manifest")
        prefix = f"derived/{file_id}/office/{job['job_id']}/"
        for ordinal, unit in enumerate(manifest["units"]):
            name = unit["name"]
            if unit["ordinal"] != ordinal or not re.fullmatch(r"\d{6}\.txt", name) or name not in seen:
                raise IsolationError("invalid_document_block")
            with (output / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != unit["sha256"]:
                    raise IsolationError("document_block_mismatch")
            storage.put_file(prefix + name, output / name, "text/plain; charset=utf-8")
        storage.put_file(prefix + "manifest.json", output / "manifest.json", "application/json")
        storage.put_file(f"derived/{file_id}/text.txt", output / "text.txt", "text/plain; charset=utf-8")
        manifest["_object_prefix"] = prefix
        return manifest
    finally:
        storage.delete(key)


def copy_cache(file_id, source, storage, platform=None):
    from uuid import uuid4

    prefix = f"derived/{file_id}/office/{uuid4().hex}/"
    result = {"processing": "done", "cache": "private_copy", "cache_source_id": source["source_file_id"]}
    if source.get("manifest_key"):
        manifest = json.loads(storage.get_bytes(source["manifest_key"]))
        if manifest.get("parser") != PARSER_VERSION or manifest.get("status") != "ready":
            raise IsolationError("cache_version_unavailable")
        source_prefix = source["manifest_key"].rsplit("/", 1)[0] + "/"
        if not source_prefix.startswith(f"derived/{source['source_file_id']}/office/"):
            raise IsolationError("invalid_cache_source")
        for unit in manifest["units"]:
            name = unit["name"]
            if not re.fullmatch(r"\d{6}\.txt", name):
                raise IsolationError("invalid_cache_block")
            storage.copy(source_prefix + name, prefix + name)
        storage.copy(source["manifest_key"], prefix + "manifest.json")
        storage.copy(source["text_key"], f"derived/{file_id}/text.txt")
        result.update(
            extract={**source["extract"], "document_manifest_key": prefix + "manifest.json"},
            document_manifest_key=prefix + "manifest.json",
            text_key=f"derived/{file_id}/text.txt",
        )
    else:
        result["extract"] = source["extract"]
    for field, key in source.get("image_keys", {}).items():
        if not key.startswith(f"derived/{source['source_file_id']}/") or ".." in key.split("/"):
            raise IsolationError("invalid_cache_image")
        target = f"derived/{file_id}/" + key.rsplit("/", 1)[-1]
        storage.copy(key, target)
        result[field] = target
    if source.get("ocr_cache") and platform is not None:
        after, generation = -1, uuid4().hex
        while True:
            batch = platform.document_cache_pages(file_id, source["source_file_id"], after)
            records = []
            for page in batch["pages"]:
                if not page["text_key"].startswith(f"derived/{source['source_file_id']}/"):
                    raise IsolationError("invalid_cache_ocr")
                target = f"derived/{file_id}/ocr-cache/{generation}/{page['page']}.txt"
                storage.copy(page["text_key"], target)
                records.append(
                    {"page": page["page"], "source_generation": page["generation"], "generation": generation, "text_key": target}
                )
            if records:
                platform.publish_cache_pages(file_id, {"source_id": source["source_file_id"], "pages": records})
            if batch["next_after"] is None:
                break
            after = batch["next_after"]
        if source.get("ocr_text_key"):
            key = source["ocr_text_key"]
            if not key.startswith(f"derived/{source['source_file_id']}/"):
                raise IsolationError("invalid_cache_ocr")
            storage.copy(key, f"derived/{file_id}/ocr.txt")
            result.update(ocr="done", ocr_text_key=f"derived/{file_id}/ocr.txt")
    return result
