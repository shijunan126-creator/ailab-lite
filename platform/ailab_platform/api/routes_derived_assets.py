"""Service-bound derivative write leases; no model supplied storage grant."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..deps import get_db
from ..locks import user_critical_section
from ..models import DerivedAsset, FileObject, OutboxMessage, Run
from ..ports.service_auth import ServicePrincipal
from ..service_auth import require_scope
from ..storage import get_storage
from . import derived_assets as assets

router = APIRouter(prefix="/internal/derived-assets", tags=["internal"])
WRITE = require_scope("files:derived_write")


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=600)
    version: str = Field(min_length=1, max_length=100)
    max_bytes: int = Field(ge=0, le=1024**4)
    kind: Literal["cache", "native", "ocr", "capture", "transfer"] = "cache"
    job_id: str | None = Field(default=None, max_length=40)


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=600)
    token: str = Field(min_length=1, max_length=40)


def authorize(db, body, principal, attempt):
    file_id, run_id, _ = assets.source(db, body.key, getattr(body, "version", None))
    if file_id:
        job_id = getattr(body, "job_id", None)
        if not job_id:
            # Completion is bound by the grant token and immutable source below.
            if isinstance(body, Completion):
                row = db.get(DerivedAsset, body.key)
                if not row or row.token != body.token:
                    raise HTTPException(409, "derived_generation_stale")
                if row.job_id:
                    current = db.get(OutboxMessage, row.job_id)
                    if (
                        not current
                        or current.claim_token != row.attempt_token
                        or not current.claimed_until
                        or assets.aware(current.claimed_until) <= datetime.now(UTC)
                    ):
                        raise HTTPException(409, "derived_job_stale")
                return db.get(FileObject, file_id).user_id
            raise HTTPException(403, "derived_job_required")
        job = db.get(OutboxMessage, job_id)
        if (
            not job
            or job.payload.get("file_id") != file_id
            or not attempt
            or job.claim_token != attempt
            or not job.claimed_until
            or assets.aware(job.claimed_until) <= datetime.now(UTC)
        ):
            raise HTTPException(409, "derived_job_stale")
        return db.get(FileObject, file_id).user_id
    elif run_id:
        if principal.service not in {"broker", "legacy"}:
            raise HTTPException(403, "derived_run_scope_required")
        run = db.get(Run, run_id)
        if not run or (run.options or {}).get("mode", "work") != "work":
            raise HTTPException(403, "derived_run_scope_required")
        return run.user_id


@router.post("/reserve")
def reserve(
    body: Grant,
    principal: ServicePrincipal = Depends(WRITE),
    x_run_attempt: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    owner = authorize(db, body, principal, x_run_attempt)
    with user_critical_section(db, owner):
        result = assets.reserve(db, body.key, body.max_bytes, body.version, kind=body.kind, reconstructible=body.kind != "capture")
        row = db.get(DerivedAsset, body.key)
        row.job_id, row.attempt_token = body.job_id, x_run_attempt
        db.commit()
    return result


@router.post("/finish")
def finish(
    body: Completion,
    principal: ServicePrincipal = Depends(WRITE),
    x_run_attempt: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    owner = authorize(db, body, principal, x_run_attempt)
    db.rollback()
    # HEAD is outside the business transaction and never trusts the caller's size.
    info = get_storage().info(body.key)
    with user_critical_section(db, owner):
        row = assets.finish(db, body.key, body.token, info.size, info.etag, published=False)
        db.commit()
    return {"key": row.key, "bytes": row.size_bytes, "digest": row.digest}


@router.post("/abandon")
def abandon(body: Completion, principal: ServicePrincipal = Depends(WRITE), db: Session = Depends(get_db)):
    # Cancellation must still be able to clean a deleted source, so it only checks
    # the unguessable API grant; a stale token cannot delete a replacement.
    assets.abandon(db, body.key, body.token)
    db.commit()
    return {"status": "accepted"}
