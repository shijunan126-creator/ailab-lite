"""Sandbox Broker HTTP service."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from ..config import settings
from ..deps import metrics_auth
from ..metrics import BrokerStateCollector, metrics_response, register_once
from ..obs import RequestContextMiddleware, setup_logging
from ..runtime_context import RuntimeContextMiddleware
from ..sandbox import SandboxError
from ..schemas import CommitIn, CommitOut, ExecIn, ExecOut, LeaseCreate, LeaseOut
from ..service_auth import require_scope
from .render import LibraryRenderer
from .service import ArtifactRejected, BrokerOwnershipError, CapacityError, LeaseCanceledError, LeaseError, LeaseLostError, LeaseManager

setup_logging("broker")
log = logging.getLogger("ailab.broker")

_manager: LeaseManager | None = None


def get_manager() -> LeaseManager:
    global _manager
    if _manager is None:
        _manager = LeaseManager()
    return _manager


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_secrets("broker")
    settings.ensure_dirs()
    manager = get_manager()
    register_once("broker-state", BrokerStateCollector(get_manager))
    try:
        manager.recover()
        log.info("sandbox broker ready provider=%s", manager.provider.name)
    except Exception:
        log.exception("broker startup recovery failed")
        manager.close()
        raise
    try:
        yield
    finally:
        manager.close()


app = FastAPI(title="AILab Sandbox Broker", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware, service="broker")
app.add_middleware(RuntimeContextMiddleware)
router = APIRouter(prefix="/internal", tags=["leases"])


@router.post("/render", dependencies=[Depends(require_scope("sandbox:render"))])
def render_library_file(body: dict) -> dict:
    if set(body) != {"file_id", "job_id"} or not all(isinstance(v, str) and 1 <= len(v) <= 100 for v in body.values()):
        raise HTTPException(status_code=422, detail="file_id and job_id required")
    try:
        return LibraryRenderer(get_manager()).render(body["file_id"], body["job_id"])
    except (LeaseError, SandboxError) as exc:
        raise HTTPException(status_code=429 if isinstance(exc, CapacityError) else 502, detail=str(exc)) from exc


@router.post("/leases", response_model=LeaseOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_scope("sandbox:use"))])
def create_lease(body: LeaseCreate) -> dict:
    manager = get_manager()
    try:
        lease = manager.create(
            run_id=body.run_id,
            inputs=[i.model_dump() for i in body.inputs],
            image=body.image,
            ttl_seconds=body.ttl_seconds,
            wait_seconds=settings.sandbox_wait_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LeaseError as exc:
        raise HTTPException(
            status_code=(
                409
                if isinstance(exc, LeaseCanceledError)
                else 429
                if isinstance(exc, CapacityError)
                else 503
                if isinstance(exc, BrokerOwnershipError)
                else 502
            ),
            detail=exc.detail(),
        ) from exc
    return {
        "lease_id": lease["lease_id"],
        "run_id": lease["run_id"],
        "provider": lease["provider"],
        "sandbox_id": lease["sandbox_id"],
        "expires_at": lease["expires_at"],
        "inputs": lease.get("inputs", []),
        "restored": lease.get("restored"),
        "restore_error": lease.get("restore_error"),
    }


@router.get("/leases", dependencies=[Depends(require_scope("sandbox:inspect"))])
def list_leases() -> dict:
    return {"leases": get_manager().active_leases()}


@router.get("/admission/{run_id}", dependencies=[Depends(require_scope("sandbox:use"))])
def admission_status(run_id: str) -> dict:
    return get_manager().admission_status(run_id)


@router.get("/leases/{lease_id}/status", dependencies=[Depends(require_scope("sandbox:use"))])
def lease_status(lease_id: str) -> dict:
    try:
        return get_manager().status(lease_id)
    except LeaseError as exc:
        raise HTTPException(status_code=410 if isinstance(exc, LeaseLostError) else 503, detail=exc.detail()) from exc


@router.post("/leases/{lease_id}/renew", dependencies=[Depends(require_scope("sandbox:use"))])
def renew_lease(lease_id: str, ttl_seconds: int | None = Query(default=None, ge=1)) -> dict:
    try:
        return get_manager().renew(lease_id, ttl_seconds)
    except LeaseError as exc:
        raise HTTPException(status_code=410 if isinstance(exc, LeaseLostError) else 503, detail=exc.detail()) from exc
    except SandboxError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/leases/{lease_id}", dependencies=[Depends(require_scope("sandbox:use"))])
def get_lease(lease_id: str) -> dict:
    try:
        return get_manager().get(lease_id)
    except LeaseError as exc:
        raise HTTPException(status_code=410 if isinstance(exc, LeaseLostError) else 503, detail=exc.detail()) from exc


@router.post("/leases/{lease_id}/exec", response_model=ExecOut, dependencies=[Depends(require_scope("sandbox:use"))])
def exec_in_sandbox(lease_id: str, body: ExecIn) -> dict:
    try:
        result = get_manager().exec(lease_id, body.cmd, body.timeout_seconds)
    except LeaseError as exc:
        raise HTTPException(status_code=410 if isinstance(exc, LeaseLostError) else 503, detail=exc.detail()) from exc
    except SandboxError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return asdict(result)


@router.put("/leases/{lease_id}/files", dependencies=[Depends(require_scope("sandbox:use"))])
def put_file(
    lease_id: str,
    path: str = Query(..., min_length=1),
    payload: bytes = Body(..., media_type="application/octet-stream"),
) -> dict:
    try:
        get_manager().put_file(lease_id, path, payload)
    except LeaseError as exc:
        raise HTTPException(status_code=410 if isinstance(exc, LeaseLostError) else 503, detail=exc.detail()) from exc
    except SandboxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "path": path, "bytes": len(payload)}


@router.get("/leases/{lease_id}/files/list", dependencies=[Depends(require_scope("sandbox:use"))])
def list_files(lease_id: str, path: str = Query(default="/workspace")) -> dict:
    try:
        return {"files": get_manager().list_files(lease_id, path)}
    except LeaseError as exc:
        raise HTTPException(status_code=410 if isinstance(exc, LeaseLostError) else 503, detail=exc.detail()) from exc
    except SandboxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/leases/{lease_id}/files", dependencies=[Depends(require_scope("sandbox:use"))])
def get_file(lease_id: str, path: str = Query(..., min_length=1)) -> Response:
    try:
        data = get_manager().get_file(lease_id, path)
    except LeaseError as exc:
        raise HTTPException(status_code=410 if isinstance(exc, LeaseLostError) else 503, detail=exc.detail()) from exc
    except SandboxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=data, media_type="application/octet-stream")


@router.post("/leases/{lease_id}/commit", response_model=CommitOut, dependencies=[Depends(require_scope("sandbox:use"))])
def commit_artifact(lease_id: str, body: CommitIn) -> dict:
    try:
        return get_manager().commit(lease_id, body.path, body.filename, body.role)
    except LeaseError as exc:
        raise HTTPException(status_code=410 if isinstance(exc, LeaseLostError) else 503, detail=exc.detail()) from exc
    except ArtifactRejected as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except SandboxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # API commit failure
        raise HTTPException(status_code=502, detail=f"artifact commit failed: {exc}") from exc


@router.post("/leases/{lease_id}/previews", dependencies=[Depends(require_scope("sandbox:use"))])
def refresh_previews(lease_id: str) -> dict:
    try:
        return {"previews": get_manager().refresh_previews(lease_id)}
    except LeaseError as exc:
        raise HTTPException(status_code=410 if isinstance(exc, LeaseLostError) else 503, detail=exc.detail()) from exc
    except SandboxError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/leases/{lease_id}/snapshot", dependencies=[Depends(require_scope("sandbox:use"))])
def snapshot_workspace(lease_id: str) -> dict:
    try:
        return get_manager().snapshot(lease_id)
    except LeaseError as exc:
        raise HTTPException(status_code=410 if isinstance(exc, LeaseLostError) else 503, detail=exc.detail()) from exc
    except ArtifactRejected as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except SandboxError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/leases/{lease_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_scope("sandbox:use"))])
def release_lease(lease_id: str) -> Response:
    try:
        get_manager().release(lease_id)
    except LeaseError as exc:
        raise HTTPException(status_code=503, detail=exc.detail()) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


app.include_router(router)
from .routes_skills import router as skills_router  # noqa: E402

app.include_router(skills_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "service": "sandbox-broker", "provider": get_manager().provider.name}


@app.get("/readyz")
def readyz() -> Response:
    manager = get_manager()
    try:
        redis_ok = bool(manager.redis.ping())
    except Exception:
        redis_ok = False
    provider_ok = manager.provider.health()
    # Leases live in Redis: without it the Broker can do nothing. Without the sandbox provider it still releases,
    # snapshots and delivers for runs in progress, so it stays in its Service and reports the degradation.
    try:
        ok = redis_ok and manager.is_ready()
    except Exception:
        ok = False
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "ok": ok,
            "degraded": redis_ok and not provider_ok,
            "redis": redis_ok,
            "provider": manager.provider.name,
            "provider_healthy": provider_ok,
        },
    )


@app.get("/metrics", include_in_schema=False, dependencies=[Depends(metrics_auth)])
def metrics_endpoint() -> Response:
    return metrics_response()
