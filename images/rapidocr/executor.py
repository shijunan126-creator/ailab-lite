"""Single-process executor on the allocated OCR host. No platform DB or model API keys."""

import asyncio
import base64
import hashlib
import io
import json
import multiprocessing
import os
import secrets
import signal
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from uuid import uuid4

_lock = threading.Lock()
_engine = None
_manifest = None

# Event-loop owned admission/terminal receipts. Retain a tombstone beyond the
# maximum accepted upload/execution window; never evict a live receipt for space.
_operations = {}
_OPERATION_RETENTION = 135
_OPERATION_LIMIT = 1024


def operation_record(operation_id, *, register=False):
    now = time.monotonic()
    for key, record in list(_operations.items()):
        if record["expires"] <= now and _supervisor.active_id != key:
            del _operations[key]
    record = _operations.get(operation_id)
    if register and record is not None:
        raise HTTPException(409, "OCR operation already received")
    if record is None:
        if len(_operations) >= _OPERATION_LIMIT:
            raise HTTPException(429, "OCR receipt capacity exhausted")
        record = {"expires": now + _OPERATION_RETENTION, "canceled": not register, "terminal": False}
        _operations[operation_id] = record
    return record


def configure_session_options(opts, backend):
    # ORT retains node trace events until the session ends. A resident OCR
    # executor must not accumulate a profile for every line of every page.
    profile_prefix = os.environ.get("OCR_PROFILE_PREFIX")
    opts.enable_profiling = bool(profile_prefix)
    if profile_prefix:
        opts.profile_file_prefix = profile_prefix
    if backend == "cann":
        opts.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
    return opts


def load_engine():
    import onnxruntime as ort
    from rapidocr import RapidOCR

    manifest = json.loads(Path(os.environ["OCR_MODEL_MANIFEST"]).read_text())
    backend = os.environ.get("OCR_REQUIRED_BACKEND", "cann")
    if backend not in {"cann", "cpu"}:
        raise RuntimeError("unknown OCR backend")
    provider = "CANNExecutionProvider" if backend == "cann" else "CPUExecutionProvider"
    if provider not in ort.get_available_providers():
        raise RuntimeError("required OCR provider unavailable")
    params = {
        "EngineConfig.onnxruntime.use_cann": backend == "cann",
        "EngineConfig.onnxruntime.intra_op_num_threads": 1,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    }
    for name in ("Det", "Cls", "Rec"):
        model = manifest["models"][name]
        if not model.get("license") or not model.get("source"):
            raise RuntimeError("OCR model license and source required")
        path = Path(model["path"])
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != model["sha256"]:
            raise RuntimeError("OCR model hash mismatch")
        params[name + ".model_path"] = str(path)
    from rapidocr.inference_engine.onnxruntime.main import OrtInferSession

    original_options = OrtInferSession._init_sess_opts

    def options(config):
        return configure_session_options(original_options(config), backend)

    OrtInferSession._init_sess_opts = staticmethod(options)
    try:
        engine = RapidOCR(params=params)
        sessions = [
            engine._load_det_model().session.session,
            engine._load_cls_model().session.session,
            engine._load_rec_model().session.session,
        ]
    finally:
        OrtInferSession._init_sess_opts = staticmethod(original_options)
    if any(
        provider not in session.get_providers()
        or session.get_providers()[0] != provider
        for session in sessions
    ):
        raise RuntimeError("OCR session provider mismatch")
    failures = []
    for session in sessions:
        session.disable_fallback()
        original_run = session.run

        def checked_run(*args, _run=original_run, **kwargs):
            try:
                return _run(*args, **kwargs)
            except Exception:
                failures.append(True)
                raise

        session.run = checked_run
    engine._ailab_failures = failures
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "models": {k: v["sha256"] for k, v in manifest["models"].items()},
                "backend": backend,
                "engine": "80b427d",
                "preprocess": "bounded-v1",
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    manifest = {
        **manifest,
        "fingerprint": fingerprint,
        "backend": backend,
        "engine": "rapidocr:80b427d",
        "providers": [s.get_providers() for s in sessions],
    }
    return engine, manifest


def inference_worker(connection):
    """Keep weights warm in a killable child; credentials stay in the supervisor."""
    if hasattr(os, "setsid"):
        os.setsid()
    os.environ.pop("OCR_EXECUTOR_TOKEN", None)
    try:
        engine, manifest = load_engine()
        connection.send({"ready": True, "manifest": manifest})
        while True:
            data = connection.recv_bytes(2 * 1024 * 1024)
            from PIL import Image

            Image.MAX_IMAGE_PIXELS = 16_000_000
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 16_000_000 or image.format not in {
                    "PNG",
                    "JPEG",
                    "WEBP",
                }:
                    raise ValueError("image_limit")
                image.verify()
            started = time.monotonic()
            engine._ailab_failures.clear()
            result = engine(data)
            if engine._ailab_failures:
                raise ValueError("inference_failed")
            text = "\n".join(result.txts or [])
            if len(text) > 200000:
                raise ValueError("text_limit")
            connection.send(
                {
                    "text": text,
                    "engine": manifest["engine"],
                    "fingerprint": manifest["fingerprint"],
                    "backend": manifest["backend"],
                    "seconds": time.monotonic() - started,
                }
            )
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        try:
            connection.send({"error": "inference_failed"})
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()


class InferenceProcess:
    def __init__(self):
        self.process = self.connection = None
        self.active_id = None
        self.cancel = threading.Event()
        self.lifecycle_lock = threading.RLock()

    def stop(self):
        with self.lifecycle_lock:
            self._stop()

    def _stop(self):
        process, self.process = self.process, None
        connection, self.connection = self.connection, None
        if connection:
            connection.close()
        if process:
            if process.is_alive():
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                except ProcessLookupError:
                    pass
                process.join(timeout=1)
                if process.is_alive():
                    process.kill()
            process.join(timeout=2)
            process.close()

    def start(self, deadline=None):
        with self.lifecycle_lock:
            self._start(deadline)

    def _start(self, deadline):
        global _manifest
        if self.process and self.process.is_alive():
            return
        self.stop()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(target=inference_worker, args=(child,), daemon=True)
        self.process, self.connection = process, parent
        process.start()
        child.close()
        until = min(deadline or time.time() + 120, time.time() + 120)
        while not parent.poll(0.1):
            if time.time() >= until or self.cancel.is_set() or not process.is_alive():
                self.stop()
                raise RuntimeError("OCR engine startup timeout or cancellation")
        message = parent.recv()
        if not message.get("ready"):
            self.stop()
            raise RuntimeError("OCR engine startup failed")
        _manifest = message["manifest"]


_supervisor = InferenceProcess()


@asynccontextmanager
async def lifespan(app):
    if not os.environ.get("OCR_EXECUTOR_TOKEN"):
        raise RuntimeError("OCR executor credential required")
    await asyncio.to_thread(_supervisor.start)
    try:
        yield
    finally:
        _supervisor.cancel.set()
        await asyncio.to_thread(_supervisor.stop)


app = FastAPI(lifespan=lifespan)


class Input(BaseModel):
    image: str = Field(max_length=4 * ((2 * 1024 * 1024 + 2) // 3))
    required_backend: str = "cann"


def authorize(authorization):
    if not secrets.compare_digest(
        authorization, "Bearer " + os.environ["OCR_EXECUTOR_TOKEN"]
    ):
        raise HTTPException(403)


@app.get("/ready")
def ready():
    return {
        "ready": bool(_supervisor.process and _supervisor.process.is_alive()),
        "backend": _manifest["backend"],
        "fingerprint": _manifest["fingerprint"],
        "providers": _manifest["providers"],
    }


async def infer(request, data, backend, operation_id, deadline, receipt=None):
    if backend != _manifest["backend"]:
        raise HTTPException(503, "required OCR backend unavailable")
    if not data or len(data) > 2 * 1024 * 1024:
        raise HTTPException(413)
    if not _lock.acquire(blocking=False):
        raise HTTPException(429, "OCR busy")
    completed = False
    try:
        # A fault/explicit cancellation destroys only the owned inference child;
        # successful requests keep the same warm process and model sessions.
        if receipt is not None and (
            _operations.get(operation_id) is not receipt or receipt["canceled"] or receipt["terminal"] or time.time() >= deadline
        ):
            raise HTTPException(408, "OCR canceled or timed out")
        _supervisor.active_id = operation_id
        _supervisor.cancel.clear()
        await asyncio.to_thread(_supervisor.start, deadline)
        if _supervisor.cancel.is_set() or time.time() >= deadline:
            raise HTTPException(408, "OCR canceled or timed out")
        await asyncio.to_thread(_supervisor.connection.send_bytes, data)
        while True:
            if (
                time.time() >= deadline
                or _supervisor.cancel.is_set()
                or await request.is_disconnected()
            ):
                raise HTTPException(408, "OCR canceled or timed out")
            if _supervisor.connection.poll():
                result = _supervisor.connection.recv()
                if "error" in result:
                    raise HTTPException(502, "OCR inference failed")
                completed = True
                return result
            if not _supervisor.process.is_alive():
                raise HTTPException(502, "OCR inference process exited")
            await asyncio.sleep(0.05)
    finally:
        try:
            if not completed:
                _supervisor.cancel.set()
                await asyncio.to_thread(_supervisor.stop)
        finally:
            _supervisor.active_id = None
            _lock.release()


@app.post("/recognize")
async def recognize(
    body: Input, request: Request, authorization: str = Header(default="")
):
    authorize(authorization)
    try:
        data = base64.b64decode(body.image, validate=True)
    except ValueError:
        raise HTTPException(422, "invalid image") from None
    return await infer(
        request, data, body.required_backend, uuid4().hex, time.time() + 90
    )


@app.post("/recognize-image")
async def recognize_image(
    request: Request,
    authorization: str = Header(default=""),
    x_ocr_operation: str = Header(min_length=1, max_length=100),
    x_ocr_deadline: float = Header(allow_inf_nan=False),
    x_ocr_backend: str = Header(default="cann"),
):
    authorize(authorization)
    if x_ocr_deadline <= time.time():
        raise HTTPException(408, "OCR deadline expired")
    receipt = operation_record(x_ocr_operation, register=True)
    deadline = min(x_ocr_deadline, time.time() + 120)
    try:
        data = bytearray()
        async with asyncio.timeout(max(0, deadline - time.time())):
            async for chunk in request.stream():
                if receipt["canceled"]:
                    raise HTTPException(408, "OCR canceled")
                if len(data) + len(chunk) > 2 * 1024 * 1024:
                    raise HTTPException(413)
                data.extend(chunk)
            return await infer(request, data, x_ocr_backend, x_ocr_operation, deadline, receipt)
    except TimeoutError:
        raise HTTPException(408, "OCR deadline expired") from None
    finally:
        receipt["terminal"] = True


@app.delete("/operations/{operation_id}")
async def cancel_operation(operation_id: str, authorization: str = Header(default="")):
    authorize(authorization)
    if not 1 <= len(operation_id) <= 100:
        raise HTTPException(422, "invalid OCR operation")
    receipt = operation_record(operation_id)
    receipt["canceled"] = True
    if _supervisor.active_id == operation_id:
        _supervisor.cancel.set()
        until = time.monotonic() + 4
        while _supervisor.active_id == operation_id:
            if time.monotonic() >= until:
                raise HTTPException(503, "OCR cancellation pending")
            await asyncio.sleep(0.05)
    return {"status": "stopped"}
