"""Single-process executor on the allocated OCR host. No platform DB or model API keys."""

import base64
import hashlib
import io
import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

_lock = threading.Lock()
_engine = None
_manifest = None


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
        opts = original_options(config)
        opts.enable_profiling = True
        opts.profile_file_prefix = os.environ.get(
            "OCR_PROFILE_PREFIX", "/tmp/ailab-ocr-profile"
        )
        if backend == "cann":
            opts.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
        return opts

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


@asynccontextmanager
async def lifespan(app):
    global _engine, _manifest
    if not os.environ.get("OCR_EXECUTOR_TOKEN"):
        raise RuntimeError("OCR executor credential required")
    _engine, _manifest = load_engine()
    yield


app = FastAPI(lifespan=lifespan)


class Input(BaseModel):
    image: str = Field(max_length=12 * 1024 * 1024)
    required_backend: str = "cann"


@app.get("/ready")
def ready():
    return {
        "ready": _engine is not None,
        "backend": _manifest["backend"],
        "fingerprint": _manifest["fingerprint"],
        "providers": _manifest["providers"],
    }


@app.post("/recognize")
def recognize(body: Input, authorization: str = Header(default="")):
    if not secrets.compare_digest(
        authorization, "Bearer " + os.environ["OCR_EXECUTOR_TOKEN"]
    ):
        raise HTTPException(403)
    if body.required_backend != _manifest["backend"]:
        raise HTTPException(503, "required OCR backend unavailable")
    if not _lock.acquire(blocking=False):
        raise HTTPException(429, "OCR busy")
    try:
        from PIL import Image

        data = base64.b64decode(body.image, validate=True)
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(413)
        Image.MAX_IMAGE_PIXELS = 16_000_000
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 16_000_000:
                raise HTTPException(413)
        started = time.monotonic()
        _engine._ailab_failures.clear()
        result = _engine(data)
        if _engine._ailab_failures:
            raise HTTPException(502, "OCR inference failed")
        return {
            "text": "\n".join(result.txts or []),
            "engine": _manifest["engine"],
            "fingerprint": _manifest["fingerprint"],
            "backend": _manifest["backend"],
            "seconds": time.monotonic() - started,
        }
    finally:
        _lock.release()
