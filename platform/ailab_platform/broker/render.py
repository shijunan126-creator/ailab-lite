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

    def _bundle(self, handle, directory: str, names: list[str], deadline: float, *, pdf_name: str | None = None):
        """Pack ``names`` once in the sandbox, download one archive and yield each validated member as (name, path, sha256).

        One transfer avoids a provider round trip per page. The archive is untrusted, so members are never extracted
        by path: names, types and sizes are checked first, then each is streamed to its own local file in ``names`` order.
        """
        remaining = int(deadline - time.monotonic())
        if remaining <= 1:
            raise LeaseError("render_timeout")
        archive = f"{directory}.tar"
        args = " ".join(shlex.quote(name) for name in names)
        packed = self.manager.exec(handle.lease_id, f"COPYFILE_DISABLE=1 tar -C {directory} -cf {archive} -- {args}", remaining)
        if packed.exit_code != 0 or time.monotonic() >= deadline:
            raise LeaseError("render_failed")
        with self.manager.transfer_file() as local:
            self.manager._download(self.manager.get(handle), archive, local, 65 * 1024 * 1024)
            with tarfile.open(local, "r:") as bundle:
                members = {m.name: m for m in bundle.getmembers()}
                if (
                    len(members) != len(names)
                    or set(members) != set(names)
                    or any(
                        not m.isfile() or not 0 <= m.size <= (32 * 1024 * 1024 if m.name == pdf_name else settings.max_sandbox_read_bytes)
                        for m in members.values()
                    )
                    or sum(m.size for m in members.values() if m.name != pdf_name) > (32 if pdf_name else 64) * 1024 * 1024
                ):
                    raise LeaseError("invalid_render_output")
                for name in names:
                    if time.monotonic() >= deadline:
                        raise LeaseError("render_timeout")
                    source = bundle.extractfile(members[name])
                    if source is None:
                        raise LeaseError("invalid_render_output")
                    with source, self.manager.transfer_file() as asset:
                        digest = hashlib.sha256()
                        with asset.open("wb") as out:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                digest.update(chunk)
                                out.write(chunk)
                        yield name, asset, digest.hexdigest()

    def render(self, file_id: str, job_id: str) -> dict:
        file = self.manager.platform.get_file(file_id, job="preview", preview_job=job_id)
        owner = file.get("render_owner_id")
        if not isinstance(owner, str) or not owner:
            raise LeaseError("render job has no authorized owner")
        priority = 0 if (file.get("meta") or {}).get("ocr_render_job_id") == job_id else 1
        with self.manager.render_activity(owner, priority=priority):
            return self._render(file_id, job_id, owner)

    def _render(self, file_id: str, job_id: str, scheduled_owner: str) -> dict:
        started = time.monotonic()
        deadline = started + 120
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

        owner = file.get("render_owner_id")
        if owner != scheduled_owner:
            raise LeaseError("render job owner changed while waiting")
        principal = LeasePrincipal(user_id=owner, run_id=f"preview:{job_id}", system=True)
        acquire_started = time.monotonic()
        spec = self._spec()
        handle, lease_source = self.manager.acquire_render(spec, principal, prepare)
        # Preparation injects the input inside acquisition. Report disjoint
        # phases so adding the timings does not count the transfer twice.
        phases["acquire_seconds"] = max(0.0, time.monotonic() - acquire_started - phases.get("inject_seconds", 0.0))
        lease_id = handle.lease_id
        returned = False
        first_asset = None

        def publish_page(name, page_count, local, digest=None):
            if digest is None:
                with local.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
            key = f"derived/{file_id}/preview/{name}"
            self.manager.storage.put_file(key, local, "image/png")
            asset = {"key": key, "content_type": "image/png", "sha256": digest}
            try:
                self.manager.platform.publish_preview_page(file_id, job_id, name, page_count, asset)
            except Exception:
                # A deleted file can race the object write after its prefix was purged.
                self.manager.storage.delete(key)
                raise
            return asset

        try:
            if pdf_mime and suffix != ".pdf":
                self.manager.exec(lease_id, f"cp {shlex.quote(source)} /workspace/.ailab/input.pdf", 5)
                source = "/workspace/.ailab/input.pdf"
            if visual:
                # Convert once, publish the first requested page, then rasterize the
                # remaining pages from that same PDF in the normal bulk path.
                first_page = selected[0] if selected else 1
                save_pdf = not source.endswith(".pdf")
                first_started = time.monotonic()
                first = self.manager.exec(
                    lease_id,
                    "AILAB_PAGE_PNG=1 AILAB_LO_PROFILE=/tmp/lo-profile AILAB_LO_LISTENER=1 AILAB_LO_STRICT=1 "
                    f"timeout -k 2 90s python /workspace/.ailab/render_preview.py {shlex.quote(source)} "
                    f"/workspace/.ailab/first --pages {first_page}{' --save-pdf' if save_pdf else ''}",
                    95,
                )
                if first.exit_code:
                    raise LeaseError("render_failed")
                try:
                    first_manifest = json.loads((first.stdout.strip().splitlines() or ["{}"])[-1])
                    if isinstance(first_manifest, dict) and first_manifest.get("error"):
                        raise LeaseError("render_failed")
                    names = first_manifest["pages"]
                    count = first_manifest["page_count"]
                    if (
                        first_manifest.get("kind") != "pages"
                        or not isinstance(names, list)
                        or len(names) != 1
                        or not isinstance(names[0], str)
                        or not re.fullmatch(r"p-\d+\.png", names[0])
                        or int(names[0][2:-4]) != first_page
                        or type(count) is not int
                        or count < first_page
                    ):
                        raise ValueError("invalid page")
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    raise LeaseError("invalid_render_output") from exc
                for key in ("convert_seconds", "raster_seconds"):
                    value = (first_manifest.get("phases") or {}).get(key)
                    if type(value) in (int, float) and 0 <= value <= 120:
                        phases["first_" + key] = value
                with self.manager.transfer_file() as local:
                    self.manager._download(
                        self.manager.get(handle), f"/workspace/.ailab/first/{names[0]}", local, settings.max_sandbox_read_bytes
                    )
                    first = publish_page(names[0], count, local)
                first_asset = (names[0], first)
                phases["first_publish_seconds"] = time.monotonic() - first_started
                if save_pdf:
                    if first_manifest.get("pdf_cache") != "document.pdf":
                        raise LeaseError("render_pdf_cache_missing")
                    source = "/workspace/.ailab/first/document.pdf"
                page_numbers = selected if selected else list(range(1, min(count, 40) + 1))
                pending_pages = [page for page in page_numbers if page != first_page]
                assets = {first_asset[0]: first_asset[1]}
                names_by_page = {first_page: first_asset[0]}
                if cache_key and not cached_pdf:
                    cache_started = time.monotonic()
                    self.manager.store_file(self.manager.get(handle), source, cache_key, "application/pdf", 32 * 1024 * 1024)
                    phases["pdf_export_seconds"] = time.monotonic() - cache_started
                if pending_pages:
                    current = self.manager.platform.get_file(file_id, job="preview", preview_job=job_id)
                    if current.get("render_owner_id") != owner:
                        raise LeaseError("render job owner changed")
                    wanted = (current.get("meta") or {}).get("preview_requested_pages") or []
                    # Requests are read once: pages a waiting Run asked for are rasterized and published first,
                    # the rest follow in document order, all in one exec and one archive transfer.
                    front = [p for p in dict.fromkeys(wanted) if type(p) is int and p in pending_pages]
                    order = front + [p for p in pending_pages if p not in front]
                    remaining = int(deadline - time.monotonic())
                    if remaining <= 1:
                        raise LeaseError("render_timeout")
                    rest_started = time.monotonic()
                    result = self.manager.exec(
                        lease_id,
                        f"AILAB_PAGE_PNG=1 timeout -k 2 {remaining}s python /workspace/.ailab/render_preview.py "
                        f"{shlex.quote(source)} /workspace/.ailab/rest --pages {','.join(map(str, order))}",
                        remaining,
                    )
                    if result.exit_code:
                        raise LeaseError("render_failed")
                    try:
                        rest = json.loads((result.stdout.strip().splitlines() or ["{}"])[-1])
                        rest_names = rest["pages"]
                        if (
                            rest.get("kind") != "pages"
                            or rest.get("page_count") != count
                            or not isinstance(rest_names, list)
                            or any(not isinstance(n, str) or not re.fullmatch(r"p-\d+\.png", n) for n in rest_names)
                            or [int(n[2:-4]) for n in rest_names] != order
                        ):
                            raise ValueError("invalid pages")
                    except (ValueError, TypeError, KeyError, AttributeError) as exc:
                        raise LeaseError("invalid_render_output") from exc
                    for name, local, digest in self._bundle(handle, "/workspace/.ailab/rest", rest_names, deadline):
                        assets[name] = publish_page(name, count, local, digest)
                        names_by_page[int(name[2:-4])] = name
                    phases["remaining_pages_seconds"] = time.monotonic() - rest_started
                names = [names_by_page[page] for page in page_numbers]
                if template:
                    thumb_started = time.monotonic()
                    thumbnail = self.manager.exec(
                        lease_id,
                        "python /workspace/.ailab/render_preview.py "
                        f"{shlex.quote(source)} /workspace/.ailab/thumbnail --pages {first_page} --template-thumbnail",
                        max(1, int(120 - (time.monotonic() - started))),
                    )
                    if thumbnail.exit_code:
                        raise LeaseError("render_failed")
                    with self.manager.transfer_file() as local:
                        self.manager._download(
                            self.manager.get(handle),
                            "/workspace/.ailab/thumbnail/template-thumbnail.jpg",
                            local,
                            settings.max_sandbox_read_bytes,
                        )
                        with local.open("rb") as stream:
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                        key = f"derived/{file_id}/preview/template-thumbnail.jpg"
                        self.manager.storage.put_file(key, local, "image/jpeg")
                    assets["template-thumbnail.jpg"] = {"key": key, "content_type": "image/jpeg", "sha256": digest}
                    phases["thumbnail_seconds"] = time.monotonic() - thumb_started
                generation = self.manager.get(handle)["render_generation"]
                returned = True
                return {
                    "preview": {"status": "ready", "kind": "pages", "pages": names, "page_count": count},
                    "assets": assets,
                    "render_lease_id": lease_id,
                    "render_generation": generation,
                }
            if time.monotonic() - started >= 120:
                raise LeaseError("render_timeout")
            remaining = max(1, int(120 - (time.monotonic() - started)))
            exec_started = time.monotonic()
            result = self.manager.exec(
                lease_id,
                "AILAB_LO_PROFILE=/tmp/lo-profile AILAB_LO_LISTENER=1 AILAB_LO_STRICT=1 "
                f"timeout -k 2 {remaining}s python /workspace/.ailab/render_preview.py "
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
            for name, local, digest in self._bundle(handle, "/workspace/.ailab/preview", names, deadline, pdf_name=pdf_name):
                is_pdf = name == pdf_name
                key = cache_key if is_pdf else f"derived/{file_id}/preview/{name}"
                self.manager.storage.put_file(key, local, "application/pdf" if is_pdf else mime)
                if not is_pdf:
                    assets[name] = {"key": key, "content_type": mime, "sha256": digest}
                    if kind == "pages" and name != "template-thumbnail.jpg":
                        self.manager.platform.publish_preview_page(file_id, job_id, name, preview["page_count"], assets[name])
            phases["export_seconds"] = time.monotonic() - export_started
            generation = self.manager.get(handle)["render_generation"]
            returned = True
            return {
                "preview": preview,
                "assets": assets,
                "render_lease_id": lease_id,
                "render_generation": generation,
            }
        finally:
            release_started = time.monotonic()
            if not returned:
                self.manager.release(handle, reason="preview_failed")
            phases["release_seconds"] = time.monotonic() - release_started
            phases["total_seconds"] = time.monotonic() - started
            log.info("library render lease_source=%s phases=%s", lease_source, {k: round(v, 3) for k, v in phases.items()})
