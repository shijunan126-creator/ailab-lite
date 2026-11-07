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
        if not run or run.status != "running" or (run.options or {}).get("mode", "work") != "work":
            raise HTTPException(403, "derived_run_scope_required")
        return run.user_id
    # Ownerless keys (web-images/) are written only by the API itself; no service grant reaches them.
    raise HTTPException(403, "derived_scope_required")


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


class BatchGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Grant] = Field(min_length=1, max_length=32)


class BatchCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Completion] = Field(min_length=1, max_length=32)


def unique_items(items):
    if len({item.key for item in items}) != len(items):
        raise HTTPException(422, "duplicate_derived_keys")


@router.post("/reserve-batch")
def reserve_batch(
    body: BatchGrant,
    principal: ServicePrincipal = Depends(WRITE),
    x_run_attempt: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    unique_items(body.items)
    owners = {authorize(db, item, principal, x_run_attempt) for item in body.items}
    if len(owners) != 1:
        raise HTTPException(403, "derived_batch_owner_mismatch")
    with user_critical_section(db, next(iter(owners))):
        result = []
        for item in body.items:
            authorize(db, item, principal, x_run_attempt)
            grant = assets.reserve(db, item.key, item.max_bytes, item.version, kind=item.kind, reconstructible=item.kind != "capture")
            row = db.get(DerivedAsset, item.key)
            row.job_id, row.attempt_token = item.job_id, x_run_attempt
            result.append(grant)
        db.commit()
    return {"items": result}


@router.post("/finish-batch")
def finish_batch(
    body: BatchCompletion,
    principal: ServicePrincipal = Depends(WRITE),
    x_run_attempt: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    unique_items(body.items)
    owners = {authorize(db, item, principal, x_run_attempt) for item in body.items}
    if len(owners) != 1:
        raise HTTPException(403, "derived_batch_owner_mismatch")
    db.rollback()
    storage = get_storage()
    infos = [(item, storage.info(item.key)) for item in body.items]
    with user_critical_section(db, next(iter(owners))):
        result = []
        for item, info in infos:
            authorize(db, item, principal, x_run_attempt)
            row = assets.finish(db, item.key, item.token, info.size, info.etag, published=False)
            result.append({"key": row.key, "bytes": row.size_bytes, "digest": row.digest})
        db.commit()
    return {"items": result}


@router.post("/abandon-batch")
def abandon_batch(body: BatchCompletion, principal: ServicePrincipal = Depends(WRITE), db: Session = Depends(get_db)):
    unique_items(body.items)
    for item in body.items:
        assets.abandon(db, item.key, item.token)
    result = []
    for item in body.items:
        row = db.get(DerivedAsset, item.key)
        if row and row.token == item.token and row.state == "deleting":
            result.append(item.model_dump())
    db.commit()
    return {"status": "accepted", "deletable": result}


@router.post("/discard-batch")
def discard_batch(body: BatchCompletion, principal: ServicePrincipal = Depends(WRITE), db: Session = Depends(get_db)):
    """Settle a deleted transport object; requires its original grant, never a raw key."""
    unique_items(body.items)
    storage = get_storage()
    for item in body.items:
        db.rollback()
        if storage.exists(item.key):
            raise HTTPException(409, "derived_object_still_present")
        assets.budget_lock(db)
        row = db.get(DerivedAsset, item.key, populate_existing=True)
        if row is None:
            db.rollback()
            continue
        if row.token != item.token or row.state not in {"writing", "staged", "deleting"}:
            raise HTTPException(409, "derived_generation_stale")
        assets.forget_deleted(db, item.key)
        db.commit()
    return {"status": "discarded"}


class WriterCleanup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=40)


@router.post("/abandon-writer")
def abandon_writer(
    body: WriterCleanup,
    principal: ServicePrincipal = Depends(WRITE),
    x_run_attempt: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    # Cleanup remains valid after deletion/claim loss, but can only affect the
    # authenticated writer's unpredictable attempt pair and unpublished states.
    if not x_run_attempt:
        raise HTTPException(409, "derived_job_stale")
    from sqlalchemy import select, update

    assets.budget_lock(db)
    db.execute(
        update(DerivedAsset)
        .where(
            DerivedAsset.job_id == body.job_id, DerivedAsset.attempt_token == x_run_attempt, DerivedAsset.state.in_(["writing", "staged"])
        )
        .values(state="deleting", lease_until=None, expires_at=datetime.now(UTC))
    )
    rows = db.scalars(
        select(DerivedAsset)
        .where(
            DerivedAsset.job_id == body.job_id,
            DerivedAsset.attempt_token == x_run_attempt,
            DerivedAsset.state == "deleting",
            DerivedAsset.lease_until.is_(None),
        )
        .order_by(DerivedAsset.key)
        .limit(32)
    ).all()
    result = [{"key": row.key, "token": row.token} for row in rows]
    db.commit()
    return {"deletable": result}


class RunPublication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Completion] = Field(min_length=1, max_length=64)


@router.post("/publish-run")
def publish_run(body: RunPublication, principal: ServicePrincipal = Depends(WRITE), db: Session = Depends(get_db)):
    if principal.service not in {"broker", "legacy"}:
        raise HTTPException(403, "derived_run_scope_required")
    unique_items(body.items)
    owners = {authorize(db, item, principal, None) for item in body.items}
    if len(owners) != 1:
        raise HTTPException(403, "derived_batch_owner_mismatch")
    with user_critical_section(db, next(iter(owners))):
        run_ids = set()
        for item in body.items:
            authorize(db, item, principal, None)
            row = db.get(DerivedAsset, item.key, populate_existing=True)
            if not row or not row.run_id or row.token != item.token or row.state not in {"staged", "ready"}:
                raise HTTPException(409, "derived_generation_stale")
            run_ids.add(row.run_id)
        if len(run_ids) != 1:
            raise HTTPException(403, "derived_batch_owner_mismatch")
        for item in body.items:
            assets.publish(db, item.key, item.token)
        db.commit()
    return {"status": "ready"}


class CacheCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=600)
    version: str = Field(min_length=1, max_length=100)


@router.post("/ready")
def ready(body: CacheCheck, principal: ServicePrincipal = Depends(WRITE), db: Session = Depends(get_db)):
    file_id, run_id, _ = assets.source(db, body.key, body.version)
    if file_id or not run_id or principal.service not in {"broker", "legacy"}:
        raise HTTPException(403, "derived_run_scope_required")
    row = db.get(DerivedAsset, body.key)
    valid = bool(
        row
        and row.state == "ready"
        and row.source_version == body.version
        and (not row.expires_at or assets.aware(row.expires_at) > datetime.now(UTC))
    )
    return {"ready": valid}
