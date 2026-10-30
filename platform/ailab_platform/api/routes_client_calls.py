"""Durable, owner-claimed browser execution requests."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import metrics
from ..config import settings
from ..deps import current_user, get_db
from ..events import append_event
from ..locks import user_critical_section
from ..models import ClientCall, Run, RunStatus, User
from ..service_auth import require_scope
from .routes_runs import _get_owned_run
from .runtime_fence import fenced_run

router = APIRouter(tags=["client-calls"])


class Payload(BaseModel):
    code: str
    input: Any = None
    timeout_ms: int = 2000


class CreateCall(BaseModel):
    call_id: str = Field(min_length=1, max_length=160)
    kind: Literal["js"]
    payload: Payload


class Error(BaseModel):
    type: Literal["timeout", "memory", "syntax", "runtime", "output_limit", "unavailable"]
    message: str


class Stats(BaseModel):
    ms: float = Field(ge=0, allow_inf_nan=False)
    heap_kb: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Result(BaseModel):
    claim_token: str = Field(min_length=1, max_length=64)
    ok: bool
    value: str | None = None
    logs: str | None = None
    error: Error | None = None
    stats: Stats


def size(value):
    return len(value.encode("utf-8"))


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def expire(db, call, run):
    now = datetime.now(UTC)
    if call.status in {"pending", "claimed"}:
        if run.status != RunStatus.RUNNING:
            call.status = "canceled"
        elif now >= utc(call.claim_deadline if call.status == "pending" else call.deadline):
            message = "没有在线的浏览器页面执行，页面可能已关闭或离线" if call.status == "pending" else "浏览器执行超时"
            call.status = "expired"
            call.result = {"ok": False, "error": {"type": "unavailable", "message": message}}
            metrics.CLIENT_CALLS.labels(call.kind, "expired").inc()
            metrics.CLIENT_CALL_SECONDS.labels(call.kind, "expired").observe(max(0, (now - utc(call.created_at)).total_seconds()))
        db.flush()


def serialize(call):
    result = call.result
    if call.status == "canceled":
        result = {"ok": False, "error": {"type": "unavailable", "message": "运行已取消，浏览器执行已停止"}}
    return {"call_id": call.call_id, "kind": call.kind, "status": call.status, "deadline": utc(call.deadline).isoformat(), "result": result}


@router.post("/internal/runs/{run_id}/client-calls", dependencies=[Depends(require_scope("runs:client_calls"))])
def create_call(run_id: str, body: CreateCall, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    payload = body.payload
    try:
        input_bytes = size(json.dumps(payload.input, ensure_ascii=False, allow_nan=False))
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(422, "invalid client input") from None
    if size(payload.code) > settings.client_js_code_max_bytes or input_bytes > settings.client_js_input_max_bytes:
        raise HTTPException(413, "client call exceeds limit")
    if not 100 <= payload.timeout_ms <= settings.client_js_max_timeout_ms:
        raise HTTPException(422, "invalid timeout")
    with fenced_run(db, run, x_run_attempt):
        existing = db.get(ClientCall, (run_id, body.call_id))
        if existing:
            expire(db, existing, run)
            db.commit()
            return serialize(existing)
        if run.status != RunStatus.RUNNING or (run.options or {}).get("client") != "portal" or not settings.client_js_enabled:
            raise HTTPException(409, "client call unavailable")
        now = datetime.now(UTC)
        call = ClientCall(
            run_id=run_id,
            call_id=body.call_id,
            kind=body.kind,
            status="pending",
            created_at=now,
            claim_deadline=now + timedelta(seconds=settings.client_js_claim_timeout_seconds),
            deadline=now + timedelta(seconds=payload.timeout_ms / 1000 + 10),
        )
        db.add(call)
        db.flush()
        append_event(
            db,
            run_id,
            "client.call",
            {
                "call_id": body.call_id,
                "kind": body.kind,
                **payload.model_dump(),
                "limits": {"memory_mb": 32, "stack_kb": 512, "output_kb": 64},
                "deadline": call.deadline.isoformat(),
            },
            commit=False,
        )
        db.commit()
        return serialize(call)


@router.get("/internal/runs/{run_id}/client-calls/{call_id}", dependencies=[Depends(require_scope("runs:client_calls"))])
def get_call(run_id: str, call_id: str, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    with user_critical_section(db, run.user_id):
        db.refresh(run)
        call = db.get(ClientCall, (run_id, call_id))
        if not call:
            raise HTTPException(404, "client call not found")
        expire(db, call, run)
        db.commit()
        return serialize(call)


@router.post("/v1/runs/{run_id}/client-calls/{call_id}/claim")
def claim_call(run_id: str, call_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = _get_owned_run(db, run_id, user)
    with user_critical_section(db, user.id):
        db.refresh(run)
        call = db.get(ClientCall, (run_id, call_id))
        if not call:
            raise HTTPException(404, "client call not found")
        expire(db, call, run)
        if call.status != "pending":
            db.commit()
            raise HTTPException(409, "client call unavailable")
        token = uuid4().hex
        won = db.execute(
            update(ClientCall)
            .where(ClientCall.run_id == run_id, ClientCall.call_id == call_id, ClientCall.status == "pending")
            .values(status="claimed", claim_token=token)
        ).rowcount
        db.commit()
        if not won:
            raise HTTPException(409, "client call unavailable")
        return {"claim_token": token}


@router.post("/v1/runs/{run_id}/client-calls/{call_id}/result")
def submit_result(run_id: str, call_id: str, body: Result, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if (
        (body.value is not None and size(body.value) > settings.client_js_value_max_bytes)
        or (body.logs is not None and size(body.logs) > settings.client_js_logs_max_bytes)
        or (body.error is not None and size(body.error.message) > settings.client_js_error_max_bytes)
    ):
        raise HTTPException(413, "client result exceeds limit")
    if body.value is not None:
        try:
            json.loads(body.value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except ValueError:
            raise HTTPException(422, "invalid JSON value") from None
    if body.ok == (body.error is not None):
        raise HTTPException(422, "invalid client result")
    run = _get_owned_run(db, run_id, user)
    with user_critical_section(db, user.id):
        db.refresh(run)
        call = db.get(ClientCall, (run_id, call_id))
        if not call:
            raise HTTPException(404, "client call not found")
        expire(db, call, run)
        if call.status != "claimed" or call.claim_token != body.claim_token:
            db.commit()
            raise HTTPException(409, "client call unavailable")
        call.status = "completed"
        call.result = (
            {"ok": True, "value": body.value, "logs": body.logs, "ms": body.stats.ms, "source": "browser"}
            if body.ok
            else {"ok": False, "error": body.error.model_dump()}
        )
        append_event(
            db,
            run_id,
            "client.result",
            {"call_id": call_id, "ok": body.ok, **({"error_type": body.error.type} if body.error else {}), "ms": body.stats.ms},
            commit=False,
        )
        db.commit()
        outcome = "ok" if body.ok else "error"
        metrics.CLIENT_CALLS.labels(call.kind, outcome).inc()
        metrics.CLIENT_CALL_SECONDS.labels(call.kind, outcome).observe(max(0, (datetime.now(UTC) - utc(call.created_at)).total_seconds()))
        return {"status": "completed"}
