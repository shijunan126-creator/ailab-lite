"""Scan first, then run every decoder in a disposable, limited process."""

from __future__ import annotations

import io
import json
import logging
import tempfile
import time
from dataclasses import asdict
from pathlib import Path, PurePath

from PIL import Image

from ..clients import PlatformClient
from ..config import settings
from ..ports.scanner import get_scanner
from ..storage import Storage
from .isolate import IsolationError, execute

log = logging.getLogger("ailab.worker")
MAX_TEXT = 200_000
MAX_THUMBNAIL_BYTES = 20 * 1024 * 1024
MAX_DECODED_PIXELS = 16_000_000
MAX_JPEG_PIXELS = 128_000_000
Image.MAX_IMAGE_PIXELS = MAX_JPEG_PIXELS


class ScanUnavailable(RuntimeError):
    pass


def scan_bytes(data: bytes) -> dict:
    result = asdict(get_scanner().scan(io.BytesIO(data)))
    return {**result, "ok": result["verdict"] == "clean"}


def download(platform: PlatformClient, file_id: str, target: Path, max_bytes: int, job: str = "processing") -> None:
    if hasattr(platform, "download_file"):
        platform.download_file(file_id, target, max_bytes=max_bytes, job=job)
    else:  # in-process test doubles
        data = platform.get_file_bytes(file_id)
        if len(data) > max_bytes:
            raise ValueError("file exceeds bounded read limit")
        target.write_bytes(data)


def _images(file_id: str, source: Path, meta: dict, storage: Storage, output: Path) -> dict:
    if (
        source.stat().st_size > MAX_THUMBNAIL_BYTES
        or meta.get("content_type") == "image/svg+xml"
        or PurePath(meta["filename"]).suffix.lower() == ".svg"
    ):
        return {}
    try:
        execute(
            "images",
            source,
            output,
            max_bytes=MAX_THUMBNAIL_BYTES,
            decoded_pixels=MAX_DECODED_PIXELS,
            jpeg_pixels=MAX_JPEG_PIXELS,
            pillow_pixels=Image.MAX_IMAGE_PIXELS,
        )
        result = {}
        for name, field in [("thumb.webp", "thumb_key"), ("display.webp", "display_key")]:
            key = f"derived/{file_id}/{name}"
            storage.put_bytes(key, (output / name).read_bytes(), "image/webp")
            result[field] = key
        return result
    except Exception:
        log.debug("image derivative unavailable for %s", file_id, exc_info=True)
        return {}


def _template_thumbnail(file_id, source, storage, output):
    key = f"derived/{file_id}/template-sketch-v3.jpg"
    try:
        if not storage.exists(key):
            execute("template_sketch", source, output)
            storage.put_file(key, output / "thumb.jpg", "image/jpeg")
        return {"thumb_key": key, "template_thumbnail": "sketch"}
    except Exception:
        # A derivative failure must never destroy successful template analysis.
        return {"template_thumbnail": "unavailable"}


def extract(data: bytes, filename: str, content_type: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="ailab-extract-") as tmp:
        source = Path(tmp) / ("input" + PurePath(filename).suffix.lower())
        source.write_bytes(data)
        try:
            return execute("extract", source, Path(tmp) / "output", filename=filename, content_type=content_type)
        except IsolationError as exc:
            return {"kind": "error", "error": str(exc), "chars": 0, "text": ""}


def process_file(
    file_id: str, platform: PlatformClient | None = None, storage: Storage | None = None, *, thumbnail_only: bool = False
) -> dict:
    if platform is not None:
        return _process_file(file_id, platform, storage, thumbnail_only=thumbnail_only)
    with PlatformClient(service="worker") as owned:  # holds a connection pool: close it with the file
        return _process_file(file_id, owned, storage, thumbnail_only=thumbnail_only)


def _process_file(file_id: str, platform: PlatformClient, storage: Storage | None, *, thumbnail_only: bool) -> dict:
    from ..storage import get_storage

    storage = storage or get_storage()
    meta = platform.get_file(file_id, job="thumbnail") if thumbnail_only else platform.get_file(file_id)
    if thumbnail_only:
        if meta.get("status") != "ready" or (meta.get("meta") or {}).get("thumb_key"):
            return {"file_id": file_id, "skipped": meta.get("status")}
        if meta.get("size", 0) > MAX_THUMBNAIL_BYTES:
            return {"file_id": file_id, "skipped": "too_large"}
    elif meta.get("status") != "pending":
        return {"file_id": file_id, "skipped": meta.get("status")}
    if not thumbnail_only and (meta.get("meta") or {}).get("scan_exhausted"):
        return {"file_id": file_id, "skipped": "scan_exhausted"}
    if not thumbnail_only and PurePath(meta["filename"]).suffix.lower() in {".pdf", ".docx", ".pptx", ".xlsx"}:
        hint = getattr(platform, "prewarm_render", None)
        if hint:
            try:
                hint()
            except Exception:
                log.warning("render prewarm hint failed")
    with tempfile.TemporaryDirectory(prefix="ailab-file-") as tmp:
        source = Path(tmp) / ("input" + PurePath(meta["filename"]).suffix.lower())
        download(
            platform,
            file_id,
            source,
            settings.max_template_bytes if (meta.get("meta") or {}).get("template_kind") else settings.max_upload_bytes,
            job="thumbnail" if thumbnail_only else "processing",
        )
        if thumbnail_only:
            thumb = _images(file_id, source, meta, storage, Path(tmp) / "images")
            if thumb:
                platform.patch_file(file_id, meta=thumb, job="thumbnail")
            return {"file_id": file_id, **thumb}
        attempts = int((meta.get("meta") or {}).get("scan_attempts", 0))
        scan_started = time.monotonic()
        with source.open("rb") as stream:
            scan = asdict(get_scanner().scan(stream))
        log.info("file processing phase=scan seconds=%.3f", time.monotonic() - scan_started)
        scan["ok"] = scan["verdict"] == "clean"
        if scan["verdict"] == "unavailable":
            platform.patch_file(
                file_id,
                meta={
                    "processing": "scan_unavailable",
                    "scan": scan,
                    "scan_attempts": attempts + 1,
                    "scan_exhausted": attempts + 1 >= settings.file_max_attempts,
                },
            )
            raise ScanUnavailable("scanner unavailable")
        if scan["verdict"] == "infected":
            platform.patch_file(file_id, meta={"processing": "rejected", "scan": scan}, status="rejected")
            return {"file_id": file_id, "rejected": scan}
        # Commit the scanner verdict before any parser starts. The API stages a
        # preview in the same transaction, independently of parsing readiness.
        scanned_file = platform.patch_file(file_id, meta={"scan": scan})
        # Give the preview the standby before the document reader competes for
        # the same reserved capacity. The durable outbox remains the retry path.
        if settings.render_prewarm and isinstance(scanned_file, dict):
            preview_meta = scanned_file.get("meta") or {}
            job_id = preview_meta.get("preview_job_id")
            if job_id and (preview_meta.get("preview") or {}).get("status") == "pending":
                from .preview import process_preview

                try:
                    result = process_preview(file_id, job_id, platform)
                    if result.get("skipped") == "in_flight":
                        deadline = time.monotonic() + 120
                        while time.monotonic() < deadline:
                            current = platform.get_file(file_id, job="preview").get("meta") or {}
                            if current.get("preview_job_id") != job_id or (current.get("preview") or {}).get("status") != "pending":
                                break
                            time.sleep(0.1)
                except Exception:
                    log.warning("early preview failed; durable preview job will retry", exc_info=True)
        if (meta.get("meta") or {}).get("template_kind"):
            try:
                analysis = execute("template", source, Path(tmp) / "template")
            except IsolationError:
                platform.patch_file(
                    file_id, meta={"scan": scan, "template_error": "模板无法解析", "processing": "failed"}, status="rejected"
                )
                return {"file_id": file_id, "failed": True}
            thumb = _template_thumbnail(file_id, source, storage, Path(tmp) / "thumbnail")
            platform.patch_file(file_id, meta={"scan": scan, "template": analysis, "processing": "done", **thumb}, status="ready")
            return {"file_id": file_id, "template": analysis}
        if (meta.get("meta") or {}).get("brand_kind"):
            kind = (meta.get("meta") or {}).get("brand_kind")
            try:
                if source.stat().st_size > settings.max_parse_bytes:
                    raise IsolationError("input_limit")
                analysis = execute("brand", source, Path(tmp) / "brand", kind=kind)
                if kind == "logo":
                    storage.put_file(f"derived/{file_id}/logo.png", Path(tmp) / "brand" / "logo.png", "image/png")
                thumb = _images(file_id, source, meta, storage, Path(tmp) / "images") if kind == "logo" else {}
                platform.patch_file(file_id, meta={"scan": scan, "brand": analysis, "processing": "done", **thumb}, status="ready")
                return {"file_id": file_id, "brand": analysis}
            except IsolationError as exc:
                platform.patch_file(file_id, meta={"scan": scan, "brand_error": str(exc), "processing": "rejected"}, status="rejected")
                return {"file_id": file_id, "rejected": str(exc)}
        cached = {}
        if hasattr(platform, "reuse_document"):
            from .office import copy_cache

            candidate = platform.reuse_document(file_id, scan)
            if candidate.get("reused"):
                cached = copy_cache(file_id, candidate["source"], storage, platform)
                if not candidate["source"].get("needs_native"):
                    platform.patch_file(file_id, meta={**cached, "scan": scan}, status="ready")
                    return {"file_id": file_id, "cache": "private_copy"}
        thumb = _images(file_id, source, meta, storage, Path(tmp) / "images")
        parse_started = time.monotonic()
        if source.stat().st_size > settings.max_parse_bytes:
            info = {"kind": "too_large", "chars": 0, "bytes": source.stat().st_size, "truncated": True}
        else:
            try:
                from . import office

                if source.suffix.lower() in office.KINDS:
                    office_output = Path(tmp) / "office"
                    manifest = office.read(file_id, platform, storage, office_output, scan)
                    prefix = manifest.pop("_object_prefix")
                    info = {
                        "kind": manifest["kind"],
                        "chars": manifest["chars"],
                        "truncated": manifest["status"] != "ready",
                        "coverage": manifest["status"],
                        "parser": manifest["parser"],
                        "units": manifest["total_units"],
                        "document_manifest_key": prefix + "manifest.json",
                        "prewritten_text": True,
                    }
                else:
                    info = execute(
                        "extract", source, Path(tmp) / "extract", filename=meta["filename"], content_type=meta.get("content_type") or ""
                    )
            except IsolationError as exc:
                info = {"kind": "error", "error": str(exc), "chars": 0}
        log.info("file processing phase=parse seconds=%.3f", time.monotonic() - parse_started)
        text = info.pop("text", "")
        skill = {k: info.pop(k) for k in ("skill", "skill_error") if k in info}
        derived = {
            "file_id": file_id,
            "filename": meta["filename"],
            "content_type": meta.get("content_type"),
            **info,
            "excerpt": text[:1000],
        }
        key = f"derived/{file_id}/meta.json"
        if info.pop("prewritten_text", False):
            derived["text_key"] = f"derived/{file_id}/text.txt"
        elif text or info.get("kind") == "text":
            derived["text_key"] = f"derived/{file_id}/text.txt"
            storage.put_bytes(derived["text_key"], text.encode("utf-8"), "text/plain; charset=utf-8")
        storage.put_bytes(key, json.dumps(derived, ensure_ascii=False).encode(), "application/json")
        # Upload does not imply OCR intent. Dedicated extraction is requested separately.
        ocr = (
            {"ocr": cached["ocr"], "ocr_text_key": cached["ocr_text_key"]}
            if cached.get("ocr") == "done" and cached.get("ocr_text_key")
            else {"ocr": "unavailable", "ocr_reason": "extraction_not_requested"}
            if (info.get("kind") == "pdf" or (meta.get("content_type") or "").startswith("image/"))
            else {}
        )
        platform.patch_file(
            file_id,
            meta={
                **cached,
                "processing": "done",
                **ocr,
                "scan": scan,
                "extract": info,
                **({"document_manifest_key": info["document_manifest_key"]} if "document_manifest_key" in info else {}),
                "derived_key": key,
                **({"text_key": derived["text_key"]} if "text_key" in derived else {}),
                "excerpt": text[:300],
                **skill,
                **thumb,
            },
            status="ready",
        )
        return {"file_id": file_id, "kind": info.get("kind"), "chars": info.get("chars", 0), "derived_key": key}
