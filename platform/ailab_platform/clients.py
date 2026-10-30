"""HTTP clients used between platform services.

Services never share a database session or a filesystem handle: Platform API is
the only writer, and the Broker is the only sandbox owner. These clients make
that boundary explicit.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import NamedTuple

import httpx

from .config import settings
from .obs import NO_REQUEST, REQUEST_ID_HEADER, request_id_var, traceparent_var
from .runtime_context import attempt_id_var, delivery_id_var
from .telemetry import span

# One client per process, not one per call: a Run makes 15-25 internal calls and
# 200 concurrent runs would otherwise build thousands of connection pools.
USAGE_RETRY_BACKOFF_SECONDS = 0.2

_LIMITS = httpx.Limits(max_connections=64, max_keepalive_connections=32, keepalive_expiry=30.0)


class ClientError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str):
        super().__init__(f"{method} {url} -> {status}: {body[:500]}")
        self.status = status
        self.body = body

    @property
    def quota_detail(self) -> dict | None:
        if self.status != 429:
            return None
        try:
            detail = json.loads(self.body).get("detail")
        except (ValueError, AttributeError):
            return None
        if (
            isinstance(detail, dict)
            and detail.get("code") == "quota_exceeded"
            and detail.get("window") in {"daily_tokens", "monthly_tokens"}
            and isinstance(detail.get("resets_at"), str)
        ):
            return detail
        return None


def _raise(method: str, url: str, resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise ClientError(method, url, resp.status_code, resp.text)


class Request(NamedTuple):
    method: str
    url: str
    timeout: float
    kwargs: dict


def _traced_kwargs(req: Request) -> dict:
    if traceparent := traceparent_var.get():
        return {**req.kwargs, "headers": {**req.kwargs["headers"], "traceparent": traceparent}}
    return req.kwargs


class _Service:
    """Describes each call once; the sync and async clients only differ in how they send it."""

    default_base_url: str = ""
    default_service: str = "api"

    def __init__(self, base_url: str | None = None, token: str | None = None, *, service: str | None = None):
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.service = service or self.default_service
        self.token = token if token is not None else settings.caller_token(self.service)

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Service-Token": self.token}

    def _req(self, method: str, path: str, timeout: float = 30, **kwargs) -> Request:
        headers = dict(self.headers)
        if attempt := attempt_id_var.get():
            headers["X-Run-Attempt"] = attempt
        if delivery := delivery_id_var.get():
            headers["X-Delivery-Id"] = delivery
        rid = request_id_var.get()
        if rid != NO_REQUEST:  # carry the correlation id into the next service's logs
            headers[REQUEST_ID_HEADER] = rid
        if traceparent := traceparent_var.get():
            headers["traceparent"] = traceparent
        return Request(method, f"{self.base_url}{path}", timeout, {"headers": headers, **kwargs})


class _PlatformApi(_Service):
    @property
    def default_base_url(self) -> str:  # type: ignore[override]
        return settings.api_base_url

    def _job_action(self, job_id, action, **body) -> Request:
        return self._req("POST", f"/internal/jobs/{job_id}/{action}", timeout=10, json=body)

    def _get_run(self, run_id: str) -> Request:
        return self._req("GET", f"/internal/runs/{run_id}")

    def _get_run_state(self, run_id: str) -> Request:
        return self._req("GET", f"/internal/runs/{run_id}/state", timeout=10)

    def _set_run_status(self, run_id, status, summary=None, error=None, result=None) -> Request:
        body = {"status": status, "summary": summary, "error": error, "result": result}
        return self._req("POST", f"/internal/runs/{run_id}/transition", json=body)

    def _append_event(self, run_id, type_, payload=None) -> Request:
        return self._req("POST", f"/internal/runs/{run_id}/events", json={"type": type_, "payload": payload or {}})

    def _reserve_usage(self, payload: dict) -> Request:
        return self._req("POST", "/internal/usage/reserve", timeout=5, json=payload)

    def _settle_usage(self, payload: dict) -> Request:
        return self._req("POST", "/internal/usage/settle", timeout=5, json=payload)

    def _get_run_usage(self, run_id: str) -> Request:
        return self._req("GET", f"/internal/runs/{run_id}/usage", timeout=5)

    def _commit_artifact(self, payload) -> Request:
        return self._req("POST", "/internal/artifacts", timeout=60, json=payload)

    def _get_file(self, file_id, run_id=None, job="processing", preview_job=None) -> Request:
        return self._req("GET", f"/internal/files/{file_id}", params=_file_params(run_id, job, preview_job))

    def _get_file_bytes(self, file_id, run_id=None, job="processing", preview_job=None) -> Request:
        return self._req("GET", f"/internal/files/{file_id}/content", timeout=120, params=_file_params(run_id, job, preview_job))

    def _patch_file(self, file_id, meta, status, job="processing") -> Request:
        return self._req("PATCH", f"/internal/files/{file_id}", json={"meta": meta, "status": status}, params={"job": job})


def _file_params(run_id: str | None, job: str, preview_job: str | None) -> dict:
    """What the API binds a file read to: the caller's run, its processing job, or a pending preview job."""
    return {"job": job, **({"run_id": run_id} if run_id else {}), **({"preview_job": preview_job} if preview_job else {})}


class PlatformClient(_PlatformApi):
    """Synchronous internal-API client (Broker / Worker)."""

    @property
    def default_service(self) -> str:
        return settings.service_name

    def __init__(self, base_url: str | None = None, token: str | None = None, *, service: str | None = None):
        super().__init__(base_url, token, service=service)
        self._http = httpx.Client(timeout=30, limits=_LIMITS, trust_env=False)

    def _send(self, req: Request) -> httpx.Response:
        with span("internal.http", method=req.method):
            r = self._http.request(req.method, req.url, timeout=req.timeout, **_traced_kwargs(req))
            _raise(req.method, req.url, r)
            return r

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> PlatformClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def summarize_file(self, file_id: str) -> dict:
        timeout = (settings.llm_total_timeout_seconds or settings.llm_timeout_seconds) + 30
        return self._send(self._req("POST", f"/internal/files/{file_id}/summary", timeout=timeout, json={})).json()

    def ocr(self, file_id: str, *, prepare: bool = False) -> dict:
        suffix = "/prepare" if prepare else ""
        # The API bounds one OCR run by its total model timeout; the read timeout must outlast it.
        timeout = (settings.llm_total_timeout_seconds or settings.llm_timeout_seconds) + 30
        return self._send(self._req("POST", f"/internal/files/{file_id}/ocr{suffix}", timeout=timeout, json={})).json()

    def get_run_state(self, run_id: str) -> dict:
        return self._send(self._get_run_state(run_id)).json()

    def get_run(self, run_id: str) -> dict:
        return self._send(self._get_run(run_id)).json()

    def thumbnail_batch(self, cursor: str, limit: int) -> dict:
        return self._send(self._req("POST", "/internal/jobs/thumbnails/backfill", json={"cursor": cursor, "limit": limit})).json()

    def transition_run(self, run_id, status, *, events=(), attempt_id=None, **values) -> dict:
        body = {"status": status, "events": list(events), "attempt_id": attempt_id, **values}
        return self._send(self._req("POST", f"/internal/runs/{run_id}/transition", json=body)).json()

    def job_action(self, job_id, action, **body) -> dict:
        return self._send(self._job_action(job_id, action, **body)).json()

    def skill_draft_action(self, draft_id: str, action: str, **body) -> dict:
        return self._send(self._req("POST", f"/internal/skill-drafts/{draft_id}/{action}", json=body)).json()

    def set_run_status(self, run_id, status, summary=None, error=None, result=None) -> dict:
        return self._send(self._set_run_status(run_id, status, summary, error, result)).json()

    def append_event(self, run_id: str, type_: str, payload: dict | None = None) -> dict:
        return self._send(self._append_event(run_id, type_, payload)).json()

    def reserve_usage(self, payload: dict) -> dict:
        return self._send(self._reserve_usage(payload)).json()

    def settle_usage(self, payload: dict) -> dict:
        return self._send(self._settle_usage(payload)).json()

    def get_run_usage(self, run_id: str) -> dict:
        return self._send(self._get_run_usage(run_id)).json()

    def commit_artifact(self, payload: dict) -> dict:
        return self._send(self._commit_artifact(payload)).json()

    def get_file(self, file_id: str, *, run_id: str | None = None, job: str = "processing", preview_job: str | None = None) -> dict:
        return self._send(self._get_file(file_id, run_id, job, preview_job)).json()

    def get_file_bytes(
        self,
        file_id: str,
        *,
        max_bytes: int | None = None,
        run_id: str | None = None,
        job: str = "processing",
        preview_job: str | None = None,
    ) -> bytes:
        req = self._get_file_bytes(file_id, run_id, job, preview_job)
        if max_bytes is None:
            return self._send(req).content
        with span("internal.download"), self._http.stream(req.method, req.url, timeout=req.timeout, **_traced_kwargs(req)) as response:
            if response.status_code >= 400:
                response.read()
                _raise(req.method, req.url, response)
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                if len(data) + len(chunk) > max_bytes:
                    raise ValueError("file exceeds bounded read limit")
                data.extend(chunk)
            return bytes(data)

    def download_file(
        self, file_id: str, target, *, max_bytes: int, job: str = "processing", run_id: str | None = None, preview_job: str | None = None
    ) -> None:
        deadline = time.monotonic() + 120
        req = self._get_file_bytes(file_id, run_id, job, preview_job)
        kwargs = _traced_kwargs(req)
        kwargs["headers"] = {**kwargs["headers"], "Accept-Encoding": "identity"}
        with span("internal.download"), self._http.stream(req.method, req.url, timeout=httpx.Timeout(10, connect=5), **kwargs) as response:
            if response.status_code >= 400:
                # Error bodies are untrusted too; do not buffer an arbitrarily
                # large HTML/proxy error while handling a bounded download.
                detail = next(response.iter_raw(), b"")[:4096].decode("utf-8", "replace")
                raise ClientError(req.method, req.url, response.status_code, detail)
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ClientError(req.method, req.url, 502, "unexpected file transfer encoding")
            size = 0
            with open(target, "wb") as stream:
                # Do not coalesce network reads into a full 64KiB block: a peer
                # trickling bytes could otherwise postpone the deadline check.
                for chunk in response.iter_raw():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("download deadline exceeded")
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("file exceeds bounded read limit")
                    stream.write(chunk)

    def complete_preview(self, file_id: str, job_id: str, preview: dict, assets: dict) -> dict:
        return self._send(
            self._req("POST", f"/internal/files/{file_id}/preview", json={"job_id": job_id, "preview": preview, "assets": assets})
        ).json()

    def prepare_document(self, file_id, scan):
        return self._send(self._req("POST", f"/internal/files/{file_id}/prepare-document", json={"scan": scan})).json()

    def read_document(self, file_id, job_id):
        req = self._req("POST", "/internal/read-document", timeout=150, json={"file_id": file_id, "job_id": job_id})
        return self._send(req._replace(url=settings.broker_base_url.rstrip("/") + "/internal/read-document")).json()

    def render_preview(self, file_id: str, job_id: str) -> dict:
        req = self._req("POST", "/internal/render", timeout=125, json={"file_id": file_id, "job_id": job_id})
        return self._send(req._replace(url=settings.broker_base_url.rstrip("/") + "/internal/render")).json()

    def document_cache_pages(self, file_id, source_id, after=-1):
        return self._send(
            self._req("GET", f"/internal/files/{file_id}/document-cache-pages", params={"source_id": source_id, "after": after})
        ).json()

    def publish_cache_pages(self, file_id, payload):
        return self._send(self._req("POST", f"/internal/files/{file_id}/document-cache-pages", json=payload)).json()

    def reuse_document(self, file_id, scan):
        return self._send(self._req("POST", f"/internal/files/{file_id}/reuse-document", json={"scan": scan})).json()

    def patch_file(self, file_id: str, meta: dict | None = None, status: str | None = None, *, job: str = "processing") -> dict:
        return self._send(self._patch_file(file_id, meta, status, job)).json()


class AsyncPlatformClient(_PlatformApi):
    """Asynchronous internal-API client (Agent)."""

    default_service = "agent"

    def __init__(self, base_url: str | None = None, token: str | None = None, *, service: str | None = None):
        super().__init__(base_url, token, service=service)
        self._http = httpx.AsyncClient(timeout=30, limits=_LIMITS, trust_env=False)

    async def _send(self, req: Request) -> httpx.Response:
        with span("internal.http", method=req.method):
            async with asyncio.timeout(req.timeout):
                r = await self._http.request(req.method, req.url, timeout=req.timeout, **_traced_kwargs(req))
            _raise(req.method, req.url, r)
            return r

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncPlatformClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def get_file_page_image(self, file_id, run_id, page):
        response = await self._send(self._req("GET", f"/internal/files/{file_id}/vision", params={"run_id": run_id, "page": page}))
        if len(response.content) > settings.ocr_page_max_bytes:
            raise ValueError("image_size_limit")
        return response.content

    async def get_file_image(self, file_id: str, run_id: str) -> bytes:
        response = await self._send(self._req("GET", f"/internal/files/{file_id}/vision", params={"run_id": run_id}))
        if len(response.content) > settings.ocr_page_max_bytes:
            raise ValueError("image_size_limit")
        return response.content

    async def get_run(self, run_id: str) -> dict:
        return (await self._send(self._req("GET", f"/internal/runs/{run_id}/model-input"))).json()

    async def create_client_call(self, run_id: str, payload: dict) -> dict:
        return (await self._send(self._req("POST", f"/internal/runs/{run_id}/client-calls", timeout=5, json=payload))).json()

    async def get_client_call(self, run_id: str, call_id: str) -> dict:
        return (await self._send(self._req("GET", f"/internal/runs/{run_id}/client-calls/{call_id}", timeout=5))).json()

    async def get_checkpoint(self, run_id: str) -> dict | None:
        return (await self._send(self._req("GET", f"/internal/runs/{run_id}/checkpoint"))).json()

    async def claim_steers(self, run_id: str) -> dict:
        return (await self._send(self._req("POST", f"/internal/runs/{run_id}/steer/claim"))).json()

    async def put_checkpoint(self, run_id: str, payload: dict) -> dict:
        return (await self._send(self._req("PUT", f"/internal/runs/{run_id}/checkpoint", json=payload))).json()

    async def pause_run(self, run_id: str, payload: dict) -> dict:
        return (await self._send(self._req("POST", f"/internal/runs/{run_id}/pause", json=payload))).json()

    async def set_conversation_title(self, run_id: str, title: str) -> dict:
        return (await self._send(self._req("PUT", f"/internal/runs/{run_id}/conversation-title", json={"title": title}))).json()

    async def library(self, run_id: str, action: str, payload: dict) -> dict:
        return (await self._send(self._req("POST", f"/internal/runs/{run_id}/library/{action}", json=payload))).json()

    async def get_model_context(self, run_id: str, after: str | None = None) -> dict:
        return (
            await self._send(self._req("GET", f"/internal/runs/{run_id}/model-context", params={"after": after} if after else {}))
        ).json()

    async def save_model_summary(self, run_id: str, payload: dict) -> dict:
        return (await self._send(self._req("PUT", f"/internal/runs/{run_id}/model-summary", json=payload))).json()

    async def get_run_state(self, run_id: str) -> dict:
        return (await self._send(self._get_run_state(run_id))).json()

    async def transition_run(self, run_id, status, *, events=(), attempt_id=None, **values) -> dict:
        body = {"status": status, "events": list(events), "attempt_id": attempt_id, **values}
        return (await self._send(self._req("POST", f"/internal/runs/{run_id}/transition", json=body))).json()

    async def job_action(self, job_id, action, **body) -> dict:
        return (await self._send(self._job_action(job_id, action, **body))).json()

    async def set_run_status(self, run_id, status, summary=None, error=None, result=None) -> dict:
        return (await self._send(self._set_run_status(run_id, status, summary, error, result))).json()

    async def append_event(self, run_id: str, type_: str, payload: dict | None = None) -> dict:
        return (await self._send(self._append_event(run_id, type_, payload))).json()

    async def _usage_retry(self, request: Request) -> dict:
        for attempt in range(4):
            try:
                async with asyncio.timeout(5):
                    return (await self._send(request)).json()
            except ClientError as exc:
                if exc.quota_detail is not None or exc.status not in {408, 429, 500, 502, 503, 504} or attempt == 3:
                    raise
            except (httpx.TransportError, TimeoutError):
                if attempt == 3:
                    raise
            await asyncio.sleep(USAGE_RETRY_BACKOFF_SECONDS * 2**attempt * random.uniform(0.5, 1.5))
        raise AssertionError("unreachable")

    async def reserve_usage(self, payload: dict) -> dict:
        return await self._usage_retry(self._reserve_usage(payload))

    async def settle_usage(self, payload: dict) -> dict:
        return await self._usage_retry(self._settle_usage(payload))

    async def get_run_usage(self, run_id: str) -> dict:
        return (await self._send(self._get_run_usage(run_id))).json()

    async def memory(self, run_id: str, payload: dict) -> dict:
        return (await self._send(self._req("POST", f"/internal/runs/{run_id}/memory", json=payload))).json()

    async def recall(self, run_id: str, payload: dict) -> dict:
        return (await self._send(self._req("POST", f"/internal/runs/{run_id}/recall", json=payload))).json()

    async def get_memory_context(self, run_id: str) -> dict:
        return (await self._send(self._req("GET", f"/internal/runs/{run_id}/memory-context"))).json()

    async def save_skill(
        self, run_id: str, name: str, mode: str, filename: str, data: bytes, *, expected_file_id: str | None = None
    ) -> dict:
        """A personal skill the model wrote; the API decides the owner from the run and keys retries by the delivery id."""
        params = {"name": name, "mode": mode, "filename": filename}
        if expected_file_id:
            params["expected_file_id"] = expected_file_id
        return (await self._send(self._req("POST", f"/internal/runs/{run_id}/skills", timeout=120, params=params, content=data))).json()

    async def get_file_bytes(
        self, file_id: str, *, run_id: str | None = None, job: str = "processing", max_bytes: int | None = None
    ) -> bytes:
        """A personal skill's file. The agent only asks for ids the API listed in the run's personal_skills."""
        req = self._get_file_bytes(file_id, run_id, job)
        if max_bytes is None:
            return (await self._send(req)).content
        async with self._http.stream(req.method, req.url, timeout=req.timeout, **_traced_kwargs(req)) as response:
            if response.status_code >= 400:
                await response.aread()
                _raise(req.method, req.url, response)
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                if len(data) + len(chunk) > max_bytes:
                    raise ValueError("file exceeds bounded read limit")
                data.extend(chunk)
            return bytes(data)


class AsyncBrokerClient(_Service):
    """Agent -> Broker: acquire a lease, run commands, move bytes, commit."""

    default_service = "agent"

    def __init__(self, base_url: str | None = None, token: str | None = None, *, service: str | None = None):
        super().__init__(base_url, token, service=service)
        self._http = httpx.AsyncClient(timeout=30, limits=_LIMITS, trust_env=False)

    @property
    def default_base_url(self) -> str:  # type: ignore[override]
        return settings.broker_base_url

    async def _raw(self, req: Request) -> httpx.Response:
        async with asyncio.timeout(req.timeout):
            return await self._http.request(req.method, req.url, timeout=req.timeout, **req.kwargs)

    async def _send(self, req: Request) -> httpx.Response:
        r = await self._raw(req)
        _raise(req.method, req.url, r)
        return r

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncBrokerClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def create_lease(self, run_id: str, ttl_seconds: int | None = None, image: str | None = None) -> dict:
        # The Broker learns the run's input files from the API, not from us.
        body = {"run_id": run_id, "image": image, "ttl_seconds": ttl_seconds}
        return (
            await self._send(
                self._req(
                    "POST",
                    "/internal/leases",
                    timeout=settings.sandbox_wait_seconds + settings.osb_request_timeout_seconds + 180,
                    json=body,
                )
            )
        ).json()

    async def renew(self, lease_id: str, ttl_seconds: int | None = None) -> dict:
        params = {"ttl_seconds": ttl_seconds} if ttl_seconds is not None else {}
        return (await self._send(self._req("POST", f"/internal/leases/{lease_id}/renew", params=params))).json()

    async def lease_status(self, lease_id: str) -> dict:
        return (await self._send(self._req("GET", f"/internal/leases/{lease_id}/status"))).json()

    async def admission_status(self, run_id: str) -> dict:
        return (await self._send(self._req("GET", f"/internal/admission/{run_id}"))).json()

    async def exec(self, lease_id: str, cmd: str, timeout_seconds: int | None = None) -> dict:
        req = self._req(
            "POST",
            f"/internal/leases/{lease_id}/exec",
            timeout=(timeout_seconds or 120) + 30,
            json={"cmd": cmd, "timeout_seconds": timeout_seconds},
        )
        return (await self._send(req)).json()

    async def copy_library_file(self, lease_id: str, file_id: str, path: str | None = None) -> dict:
        return (
            await self._send(
                self._req("POST", f"/internal/leases/{lease_id}/library-file", timeout=150, json={"file_id": file_id, "path": path})
            )
        ).json()

    async def put_file(self, lease_id: str, path: str, data: bytes) -> None:
        await self._send(self._req("PUT", f"/internal/leases/{lease_id}/files", timeout=120, params={"path": path}, content=data))

    async def put_fetched(self, lease_id: str, path: str, local: Path) -> None:
        """Streams a downloaded file from disk; neither side buffers it whole."""

        async def body():
            with local.open("rb") as f:
                while chunk := await asyncio.to_thread(f.read, 1024 * 1024):
                    yield chunk

        req = self._req("PUT", f"/internal/leases/{lease_id}/fetched", timeout=300, params={"path": path}, content=body())
        await self._send(req)

    async def get_file(self, lease_id: str, path: str) -> bytes:
        return (await self._send(self._req("GET", f"/internal/leases/{lease_id}/files", timeout=120, params={"path": path}))).content

    async def list_files(self, lease_id: str, path: str) -> list[dict]:
        r = await self._send(self._req("GET", f"/internal/leases/{lease_id}/files/list", timeout=60, params={"path": path}))
        return r.json().get("files", [])

    async def commit(self, lease_id: str, path: str, filename: str | None = None, role: str = "output") -> dict:
        body = {"path": path, "filename": filename, "role": role}
        return (await self._send(self._req("POST", f"/internal/leases/{lease_id}/commit", timeout=180, json=body))).json()

    async def refresh_previews(self, lease_id: str) -> list[dict]:
        r = await self._send(self._req("POST", f"/internal/leases/{lease_id}/previews", timeout=660))
        return r.json().get("previews", [])

    async def snapshot(self, lease_id: str) -> dict:
        return (await self._send(self._req("POST", f"/internal/leases/{lease_id}/snapshot", timeout=300))).json()

    async def release(self, lease_id: str) -> None:
        req = self._req("DELETE", f"/internal/leases/{lease_id}", timeout=60)
        r = await self._raw(req)
        if r.status_code not in (200, 204, 404):
            _raise(req.method, req.url, r)
