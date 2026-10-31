"""On-demand library rendering using LeaseManager's public lifecycle and I/O API."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shlex
import tarfile
import time
from dataclasses import replace
from pathlib import Path

from ..config import settings
from ..contracts.file_preview import LOCAL_SHEETS, PAGES, SANDBOX_SHEETS
from ..contracts.sandbox import SandboxSpec
from ..domain.admission import LeasePrincipal
from .service import LeaseError, LeaseManager

SCRIPT = Path(__file__).resolve().parents[1] / "sandbox_tools" / "render_preview.py"
log = logging.getLogger("ailab.broker.render")


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
        with self.manager.render_activity():
            return self._render(file_id, job_id)

    def _render(self, file_id: str, job_id: str) -> dict:
        started = time.monotonic()
        phases = {}
        platform = self.manager.platform
        file = platform.get_file(file_id, job="preview", preview_job=job_id)
        meta = file.get("meta") or {}
        scanned = file.get("status") == "pending" and (meta.get("scan") or {}).get("verdict") == "clean"
        if (
            not (file.get("status") == "ready" or scanned)
            or meta.get("preview_job_id") != job_id
            or (meta.get("preview") or {}).get("status") != "pending"
        ):
            raise LeaseError("preview job is not pending for this file")
        suffix = Path(file["filename"]).suffix.lower()
        pdf_mime = file.get("content_type", "").split(";")[0] == "application/pdf"
        if suffix not in PAGES | SANDBOX_SHEETS | LOCAL_SHEETS and not pdf_mime:
            raise LeaseError("unsupported sandbox preview type")
        source = "/workspace/inputs/input" + suffix
        template = meta.get("template_kind") == "pptx"
        thumbnail_option = " --template-thumbnail" if template else ""
        selected = meta.get("ocr_render_pages") if meta.get("ocr_render_job_id") == job_id else None
        if meta.get("ocr_render_job_id") == job_id and suffix in SANDBOX_SHEETS | LOCAL_SHEETS:
            thumbnail_option += " --visual-pages"
        if selected:
            if (
                not isinstance(selected, list)
                or not 1 <= len(selected) <= 20
                or any(type(n) is not int or not 1 <= n <= 100000 for n in selected)
            ):
                raise LeaseError("invalid_page_selection")
            thumbnail_option += " --pages " + ",".join(map(str, selected))

        digest = file.get("sha256", "")
        cache_key = None
        visual = suffix in PAGES or meta.get("ocr_render_job_id") == job_id
        if re.fullmatch(r"[a-f0-9]{64}", digest) and visual and suffix != ".pdf" and not pdf_mime:
            version = hashlib.sha256(SCRIPT.read_bytes() + b"\0" + self._spec().image.encode()).hexdigest()[:20]
            cache_key = f"derived/{file_id}/render-cache/{digest}/{version}/document.pdf"
        cached_pdf = bool(cache_key and self.manager.storage.exists(cache_key))
        if cached_pdf:
            source = "/workspace/inputs/cached.pdf"
        elif cache_key:
            thumbnail_option += " --save-pdf"

        def prepare(lease: dict) -> None:
            injection_started = time.monotonic()
            if cached_pdf:
                with self.manager.transfer_file() as local:
                    with self.manager.storage.open_stream(cache_key) as chunks, local.open("wb") as out:
                        total = 0
                        for chunk in chunks:
                            total += len(chunk)
                            if total > 32 * 1024 * 1024:
                                raise LeaseError("cached_pdf_limit")
                            out.write(chunk)
                    self.manager._provider_call("put_file_from_path", lease["provider_ref"], source, local, max_bytes=32 * 1024 * 1024)
            else:
                self.manager.inject_file(lease, file_id, source, job="preview", preview_job=job_id)
            self.manager.put_input(lease, "/workspace/.ailab/render_preview.py", SCRIPT.read_bytes())
            phases["inject_seconds"] = time.monotonic() - injection_started

        principal = LeasePrincipal(user_id="system:preview", run_id=f"preview:{job_id}", system=True)
        acquire_started = time.monotonic()
        spec = self._spec()
        handle = self.manager.adopt_standby(spec, principal, prepare)
        lease_source = "standby" if handle is not None else "cold"
        if handle is None:
            handle = self.manager.acquire(spec, principal, wait_seconds=30, prepare=prepare)
        # Preparation injects the input inside acquisition. Report disjoint
        # phases so adding the timings does not count the transfer twice.
        phases["acquire_seconds"] = max(0.0, time.monotonic() - acquire_started - phases.get("inject_seconds", 0.0))
        lease_id = handle.lease_id
        try:
            if pdf_mime and suffix != ".pdf":
                self.manager.exec(lease_id, f"cp {shlex.quote(source)} /workspace/.ailab/input.pdf", 5)
                source = "/workspace/.ailab/input.pdf"
            if time.monotonic() - started >= 120:
                raise LeaseError("render_timeout")
            remaining = max(1, int(120 - (time.monotonic() - started)))
            exec_started = time.monotonic()
            result = self.manager.exec(
                lease_id,
                f"AILAB_LO_PROFILE=/tmp/lo-profile timeout -k 2 {remaining}s python /workspace/.ailab/render_preview.py "
                f"{shlex.quote(source)} /workspace/.ailab/preview{thumbnail_option}",
                remaining,
            )
            phases["exec_seconds"] = time.monotonic() - exec_started
            if time.monotonic() - started >= 120 or result.exit_code != 0:
                raise LeaseError("render_timeout" if time.monotonic() - started >= 120 else "render_failed")
            try:
                manifest = json.loads((result.stdout.strip().splitlines() or ["{}"])[-1])
            except (ValueError, TypeError) as exc:
                raise LeaseError("invalid_render_output") from exc
            if not isinstance(manifest, dict):
                raise LeaseError("invalid_render_output")
            timings = manifest.get("phases")
            if isinstance(timings, dict):
                for key in ("convert_seconds", "raster_seconds"):
                    value = timings.get(key)
                    if type(value) in (int, float) and 0 <= value <= 120:
                        phases[key] = value
            if manifest.get("error"):
                raise LeaseError("render_failed")
            kind = manifest.get("kind")
            if kind == "pages":
                names = manifest.get("pages") or []
                if (
                    not isinstance(names, list)
                    or not names
                    or len(names) > 40
                    or any(not isinstance(n, str) or not re.fullmatch(r"p-\d+\.jpg", n) for n in names)
                    or len(set(names)) != len(names)
                ):
                    raise LeaseError("invalid_render_output")
                preview = {"status": "ready", "kind": "pages", "pages": names, "page_count": manifest.get("page_count")}
                mime = "image/jpeg"
                if template:
                    if manifest.get("thumbnail") != "template-thumbnail.jpg":
                        raise LeaseError("invalid_render_output")
                    names = [*names, "template-thumbnail.jpg"]
            elif kind == "sheets" and manifest.get("data") == "sheets.json":
                names, mime = ["sheets.json"], "application/json"
                preview = {"status": "ready", "kind": "sheets", "data": "sheets.json"}
            else:
                raise LeaseError("invalid_render_output")
            pdf_name = manifest.get("pdf_cache")
            if pdf_name is not None and (pdf_name != "document.pdf" or not cache_key or cached_pdf or kind != "pages"):
                raise LeaseError("invalid_render_output")
            if pdf_name:
                names = [*names, pdf_name]
            assets = {}
            export_started = time.monotonic()
            remaining = max(1, int(120 - (time.monotonic() - started)))
            archive = "/workspace/.ailab/preview.tar"
            args = " ".join(shlex.quote(name) for name in names)
            packed = self.manager.exec(lease_id, f"COPYFILE_DISABLE=1 tar -C /workspace/.ailab/preview -cf {archive} -- {args}", remaining)
            if packed.exit_code != 0 or time.monotonic() - started >= 120:
                raise LeaseError("render_failed")
            # One sandbox transfer avoids a provider round trip for every page. Never
            # extract an untrusted archive by path: validate every member first.
            with self.manager.transfer_file() as local:
                self.manager._download(self.manager.get(lease_id), archive, local, 65 * 1024 * 1024)
                with tarfile.open(local, "r:") as bundle:
                    members = bundle.getmembers()
                    if (
                        len(members) != len(names)
                        or {m.name for m in members} != set(names)
                        or any(
                            not m.isfile()
                            or not 0 <= m.size <= (32 * 1024 * 1024 if m.name == pdf_name else settings.max_sandbox_read_bytes)
                            for m in members
                        )
                        or sum(m.size for m in members if m.name != pdf_name) > 32 * 1024 * 1024
                    ):
                        raise LeaseError("invalid_render_output")
                    for member in members:
                        if time.monotonic() - started >= 120:
                            raise LeaseError("render_timeout")
                        is_pdf = member.name == pdf_name
                        key = cache_key if is_pdf else f"derived/{file_id}/preview/{member.name}"
                        source = bundle.extractfile(member)
                        if source is None:
                            raise LeaseError("invalid_render_output")
                        with source, self.manager.transfer_file() as asset:
                            digest = hashlib.sha256()
                            with asset.open("wb") as out:
                                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                    digest.update(chunk)
                                    out.write(chunk)
                            self.manager.storage.put_file(key, asset, "application/pdf" if is_pdf else mime)
                        if not is_pdf:
                            assets[member.name] = {"key": key, "content_type": mime, "sha256": digest.hexdigest()}
            phases["export_seconds"] = time.monotonic() - export_started
            return {"preview": preview, "assets": assets}
        finally:
            release_started = time.monotonic()
            self.manager.release(handle, reason="preview_complete")
            phases["release_seconds"] = time.monotonic() - release_started
            phases["total_seconds"] = time.monotonic() - started
            log.info("library render lease_source=%s phases=%s", lease_source, {k: round(v, 3) for k, v in phases.items()})
