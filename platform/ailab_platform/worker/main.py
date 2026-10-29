"""File processing Worker: parse, preview, scan, and drive OCR jobs (model inference stays in the API)."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import Depends, FastAPI, Response
from fastapi.responses import JSONResponse

from .. import metrics, obs
from ..clients import ClientError, PlatformClient
from ..config import settings
from ..contracts.jobs import JobEnvelope
from ..deps import metrics_auth
from ..metrics import metrics_response
from ..obs import NO_REQUEST, RequestContextMiddleware, request_id_var, setup_logging
from ..queue import get_queue
from .ocr import process_ocr
from .pipeline import ScanUnavailable, process_file
from .preview import process_preview

setup_logging("worker")
log = logging.getLogger("ailab.worker")
_thread: threading.Thread | None = None
_stop = threading.Event()


CONSUMER = f"worker-{socket.gethostname()}-{os.getpid()}"
FILE_VISIBILITY_SECONDS = 600


def handle_message(queue, msg) -> None:
    file_id = msg.payload.get("file_id")
    trace_context = getattr(obs, "traceparent_var", None)
    trace_token = None
    rid = request_id_var.set(str(msg.payload.get("request_id") or file_id or NO_REQUEST))  # log lines about this file carry its id
    try:
        if "schema_version" not in msg.payload:
            _handle(queue, msg, file_id)
            return
        envelope = JobEnvelope.model_validate(msg.payload)
        msg = replace(msg, payload=dict(msg.payload))
        if trace_context is not None:
            trace_token = trace_context.set(envelope.traceparent)
        if envelope.kind == "run":
            raise ValueError("run envelope on Worker stream")
        client = PlatformClient(service="worker")
        try:
            claim = client.job_action(envelope.job_id, "claim", consumer=CONSUMER)
            if claim["state"] != "claimed":
                queue.ack(settings.file_queue, msg)
                return
            token = claim["token"]
            msg.deliveries = claim["attempt"]
            # Existing pipeline takes the preview identity from the same stable job_id.
            msg.payload["preview_only"] = envelope.kind == "preview"
            msg.payload["thumbnail_only"] = envelope.kind == "thumbnail"
            msg.payload["ocr_only"] = envelope.kind == "ocr"
            msg.payload["summary_only"] = envelope.kind == "summary"

            class Completion:
                completed = False

                def ack(self, *args):
                    self.completed = True

            completion = Completion()
            from ..runtime_context import attempt_id_var

            identity = attempt_id_var.set(token)
            try:
                _handle(completion, msg, file_id)
            finally:
                attempt_id_var.reset(identity)
            if completion.completed:
                client.job_action(envelope.job_id, "ack", token=token)
                queue.ack(settings.file_queue, msg)
        finally:
            client.close()
    finally:
        if trace_token is not None:
            trace_context.reset(trace_token)
        request_id_var.reset(rid)


def _patch(file_id: str, **kw) -> None:
    client = PlatformClient(service="worker")
    try:
        client.patch_file(file_id, **kw)
    finally:
        client.close()


def _obsolete_job(exc: Exception) -> bool:
    if not isinstance(exc, ClientError) or exc.status not in {403, 404, 409}:
        return False
    try:
        return json.loads(exc.body).get("detail") in {
            "file not found",
            "file is not eligible for this processing job",
        }
    except (ValueError, AttributeError):
        return False


def _handle(queue, msg, file_id) -> None:
    preview_only = msg.payload.get("preview_only") is True
    if preview_only:
        try:
            process_preview(file_id, msg.payload.get("job_id", ""))
        except Exception:
            log.exception("preview job could not complete for %s", file_id)
            return
        queue.ack(settings.file_queue, msg)
        return
    if msg.payload.get("summary_only") is True:
        platform = PlatformClient(service="worker")
        try:
            result = platform.summarize_file(file_id)
            if result.get("status") == "failed" and result.get("attempt", 0) < 3:
                return
        except Exception as exc:
            if not _obsolete_job(exc):
                return
        finally:
            platform.close()
        queue.ack(settings.file_queue, msg)
        return
    if msg.payload.get("ocr_only") is True:
        platform = PlatformClient(service="worker")
        try:
            process_ocr(file_id, platform)
        except Exception as exc:
            if not _obsolete_job(exc):
                # Unacked: the API keeps the job pending, so redelivery resumes it without touching the file's readiness.
                log.warning("OCR for %s not finished yet; will retry", file_id, exc_info=True)
                return
        finally:
            platform.close()
        queue.ack(settings.file_queue, msg)
        return
    thumbnail_only = msg.payload.get("thumbnail_only") is True
    if file_id and msg.deliveries > settings.file_max_attempts:
        if thumbnail_only:
            log.warning("abandoning thumbnail for %s after %d attempts", file_id, msg.deliveries - 1)
            queue.ack(settings.file_queue, msg)
            return
        log.error("file %s could not be scanned after %d attempts; quarantining it", file_id, msg.deliveries - 1)
        try:
            _patch(
                file_id,
                meta={"processing": "scan_unavailable", "scan_exhausted": True, "scan_attempts": msg.deliveries - 1},
            )
        except Exception as exc:
            if _obsolete_job(exc):
                queue.ack(settings.file_queue, msg)
                return
            log.warning("could not reject file %s", file_id, exc_info=True)
            return  # leave the message unacked and try again later
        counter = getattr(metrics, "SCAN_EXHAUSTED", None)
        if counter is not None:
            counter.inc()
        metrics.FILES.labels("rejected").inc()
        queue.ack(settings.file_queue, msg)
        return
    if file_id:
        started = time.perf_counter()
        try:
            result = process_file(file_id, thumbnail_only=True) if thumbnail_only else process_file(file_id)
            log.info("processed file %s: %s", file_id, result)
            outcome = "rejected" if "rejected" in result else "skipped" if "skipped" in result else "ready"
            metrics.FILES.labels(outcome).inc()
            metrics.FILE_SECONDS.observe(time.perf_counter() - started)
        except ScanUnavailable:
            metrics.FILES.labels("error").inc()
            for attempt in range(1, settings.file_max_attempts):
                if _stop.wait(min(60, settings.scan_retry_backoff_seconds * 2 ** (attempt - 1))):
                    return
                try:
                    process_file(file_id)
                    break
                except ScanUnavailable:
                    continue
                except Exception:
                    log.exception("scan retry could not complete for %s", file_id)
                    return
            else:
                counter = getattr(metrics, "SCAN_EXHAUSTED", None)
                if counter is not None:
                    counter.inc()
            queue.ack(settings.file_queue, msg)
            return
        except Exception as exc:
            if _obsolete_job(exc):
                log.info("dropping obsolete file job %s", file_id)
                queue.ack(settings.file_queue, msg)
                return
            # Stay quarantined (pending) and un-acked: redelivered after the visibility timeout.
            log.exception("file %s processing failed", file_id)
            metrics.FILES.labels("error").inc()
            if thumbnail_only:
                return  # retry without changing an already usable file's status/processing metadata
            try:
                _patch(file_id, meta={"processing": "error"})
            except Exception:
                log.warning("could not flag file %s as errored", file_id, exc_info=True)
            return
    queue.ack(settings.file_queue, msg)


def _consume() -> None:
    while not _stop.is_set():
        queue = get_queue()
        try:
            msg = queue.dequeue(settings.file_queue, CONSUMER, 2, FILE_VISIBILITY_SECONDS)
        except Exception:
            log.exception("file queue unavailable; retrying")
            _stop.wait(3)
            continue
        if msg is None:
            continue
        try:
            handle_message(queue, msg)
        except Exception:
            log.exception("could not finish handling file message %s", msg.id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _thread
    settings.validate_secrets("worker")
    settings.ensure_dirs()
    _stop.clear()
    _thread = threading.Thread(target=_consume, name="file-worker", daemon=True)
    _thread.start()
    log.info("file worker ready")
    yield
    _stop.set()


app = FastAPI(title="AILab File Worker", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware, service="worker")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "service": "file-worker"}


@app.get("/readyz")
def readyz() -> Response:
    try:
        queue_ok = get_queue().ping()
    except Exception:
        queue_ok = False
    running = bool(_thread and _thread.is_alive())
    ok = queue_ok and running
    return JSONResponse(status_code=200 if ok else 503, content={"ok": ok, "queue": queue_ok, "worker_running": running})


@app.get("/metrics", include_in_schema=False, dependencies=[Depends(metrics_auth)])
def metrics_endpoint() -> Response:
    return metrics_response()
