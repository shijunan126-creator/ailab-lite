"""Append redacted metadata to the caller's transaction; never commit business changes here."""

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import AuditLog
from .obs import clean_request_id, request_id_var

# A positive allowlist prevents future request DTO fields from silently becoming audit secrets.
DETAIL_FIELDS = {"fields", "roles", "dept_id", "quota_policy_id", "disabled", "auth", "reason", "username", "group_by"}


def record_audit(
    db: Session,
    action: str,
    target_type: str,
    target_id: str | None = None,
    *,
    actor_id: str | None = None,
    actor_service: str | None = None,
    outcome: str = "success",
    detail: dict | None = None,
) -> None:
    safe = {}
    for key, value in (detail or {}).items():
        if key not in DETAIL_FIELDS:
            continue
        if isinstance(value, list):
            safe[key] = [str(v)[:120] for v in value[:32]]
        elif value is None or isinstance(value, bool | int):
            safe[key] = value
        elif isinstance(value, str):
            safe[key] = value[:256]
    db.add(
        AuditLog(
            actor_id=actor_id,
            actor_service=actor_service,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            request_id=clean_request_id(request_id_var.get()),
            detail=safe,
        )
    )


# Failed validation and missing-reference paths must leave the same audit trail as policy denials.


class AuditedRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            from .db import SessionLocal
            from .models import utcnow
            from .security import authenticate, bearer_token

            started = utcnow()
            try:
                return await original(request)
            except (HTTPException, RequestValidationError, IntegrityError) as exc:
                parts = request.url.path.strip("/").split("/")
                if parts[:2] == ["v1", "admin"]:
                    resource = parts[2]
                    operation = {"GET": "list", "POST": "create", "PATCH": "update", "DELETE": "delete"}[request.method]
                    action = resource + "." + ("password" if parts[-1] == "password" else operation)
                elif parts[-1] in {"login", "callback", "register", "logout", "password"}:
                    action = "users.password" if parts[-1] == "password" else "auth." + ("login" if parts[-1] == "callback" else parts[-1])
                else:
                    raise
                with SessionLocal() as db:
                    existing = db.scalar(
                        select(AuditLog.id)
                        .where(AuditLog.request_id == request_id_var.get(), AuditLog.at >= started, AuditLog.outcome == "failure")
                        .limit(1)
                    )
                    if existing is None:
                        actor = authenticate(db, bearer_token(request))
                        code = exc.status_code if isinstance(exc, HTTPException) else 409 if isinstance(exc, IntegrityError) else 422
                        record_audit(
                            db,
                            action,
                            "admin" if parts[1] == "admin" else "user",
                            actor_id=actor.id if actor else None,
                            outcome="failure",
                            detail={"reason": str(code)},
                        )
                        db.commit()
                if isinstance(exc, IntegrityError):
                    raise HTTPException(409, "conflict") from None
                raise

        return handle
