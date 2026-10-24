"""Platform API entrypoint (sole writer of the business database)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..catalog import get_catalog
from ..config import settings
from ..db import SessionLocal, init_db
from ..deps import current_user, metrics_auth
from ..metrics import ApiStateCollector, metrics_response, register_once
from ..models import User
from ..obs import RequestContextMiddleware, setup_logging
from ..queue import get_queue
from .jobs import router as jobs_router
from .preview_dispatch import dispatch_forever
from .reaper import lifecycle_cleanup_forever, reap_forever, workspace_cleanup_forever
from .routes_admin import router as admin_router
from .routes_auth import me
from .routes_auth import router as auth_router
from .routes_conversations import router as conversations_router
from .routes_files import router as files_router
from .routes_files import trash_router
from .routes_internal import router as internal_router
from .routes_library_tools import router as library_tools_router
from .routes_memory import router as memory_router
from .routes_model_context import router as model_context_router
from .routes_ocr import router as ocr_router
from .routes_ocr import vision_router
from .routes_projects import router as projects_router
from .routes_runs import router as runs_router
from .routes_runtime import router as runtime_router
from .routes_schedules import router as schedules_router
from .routes_schedules import schedules_forever
from .routes_search import router as search_router
from .routes_skills import router as skills_router

setup_logging("api")
log = logging.getLogger("ailab.api")


def warn_storage_capacity() -> None:
    from sqlalchemy import func, select

    if settings.provisioned_storage_bytes:
        with SessionLocal() as db:
            users = db.scalar(select(func.count()).select_from(User))
        budget = users * settings.library_quota_bytes
        if budget > settings.provisioned_storage_bytes:
            log.warning(
                "storage overcommitted users=%d quota_bytes=%d logical_bytes=%d provisioned_bytes=%d",
                users,
                settings.library_quota_bytes,
                budget,
                settings.provisioned_storage_bytes,
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_secrets("api")
    settings.ensure_dirs()
    get_catalog()  # fail at startup, not on the first request, if models.toml is broken
    register_once("api-state", ApiStateCollector())
    init_db()
    warn_storage_capacity()
    log.info("platform api ready db=%s storage=%s", settings.redacted()["database_url"], settings.storage_backend)
    preview_dispatch = asyncio.create_task(dispatch_forever(), name="preview-outbox")
    reaper = asyncio.create_task(reap_forever(), name="reaper")
    workspace_cleanup = asyncio.create_task(workspace_cleanup_forever(), name="workspace-cleanup")
    lifecycle_cleanup = asyncio.create_task(lifecycle_cleanup_forever(), name="lifecycle-cleanup")
    schedules = asyncio.create_task(schedules_forever(), name="schedules")
    yield
    schedules.cancel()
    lifecycle_cleanup.cancel()
    preview_dispatch.cancel()
    reaper.cancel()
    workspace_cleanup.cancel()


app = FastAPI(
    title="AILab Platform API",
    version="0.1.0",
    description="Identity, runs, files, events and the artifact ledger. Sole DB writer.",
    lifespan=lifespan,
)
app.add_middleware(RequestContextMiddleware, service="api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def reject_oversized_upload(request: Request, call_next):
    """Starlette parses the whole multipart body before the handler runs, so the size
    check inside the handler comes too late to spare the disk. Refuse on Content-Length
    first. (A chunked upload has no Content-Length: cap it at the reverse proxy.)"""
    if request.method == "POST" and request.url.path == "/v1/files":
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > settings.max_upload_bytes + 64 * 1024:
            return JSONResponse(status_code=413, content={"detail": f"file exceeds {settings.max_upload_bytes} bytes"})
    return await call_next(request)


app.include_router(auth_router)
app.include_router(admin_router)
app.add_api_route("/v1/me", me, methods=["GET"], tags=["auth"])
app.include_router(runs_router)
app.include_router(conversations_router)
app.include_router(skills_router)
app.include_router(memory_router)
app.include_router(library_tools_router)
app.include_router(files_router)
app.include_router(trash_router)
app.include_router(projects_router)
app.include_router(internal_router)
app.include_router(ocr_router)
app.include_router(vision_router)
app.include_router(model_context_router)
app.include_router(jobs_router)
app.include_router(runtime_router)
app.include_router(schedules_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "service": "platform-api", "env": settings.env}


@app.get("/readyz")
def readyz() -> dict:
    checks: dict[str, object] = {"db": False, "queue": False, "storage": False}
    code = 200
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["db"] = True
    except Exception as exc:  # pragma: no cover - operational
        checks["db_error"] = str(exc)
        code = 503
    try:
        checks["queue"] = get_queue().ping()
    except Exception as exc:  # pragma: no cover
        checks["queue_error"] = str(exc)
    checks["storage"] = settings.storage_path.exists() if settings.storage_backend == "local" else True
    if not checks["storage"]:
        code = 503
    # Not ready only when this API is useless without the dependency (database, storage mount). With one replica,
    # a Redis blip must not take login, conversations and downloads offline too: only queueing degrades.
    degraded = not checks["queue"]
    return JSONResponse(status_code=code, content={"ok": code == 200, "degraded": degraded, "checks": checks})


@app.get("/v1/info")
def info(_: User = Depends(current_user)) -> dict:
    """What this deployment runs on, for the Portal banner. Never include secrets here."""
    return {
        "env": settings.env,
        "default_model": get_catalog().default_id(),
        "sandbox_provider": settings.sandbox_provider,
        "storage_backend": settings.storage_backend,
        "max_upload_bytes": settings.max_upload_bytes,
    }


@app.get("/")
def root() -> dict:
    return {
        "service": "platform-api",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/healthz",
    }


@app.get("/metrics", include_in_schema=False, dependencies=[Depends(metrics_auth)])
def metrics_endpoint() -> Response:
    return metrics_response()


app.include_router(search_router)
