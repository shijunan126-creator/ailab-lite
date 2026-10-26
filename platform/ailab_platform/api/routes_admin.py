"""Administrative metadata only. File provenance is metadata; never joins conversation content."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..accounts import AccountError, create_user, revoke_sessions, set_password
from ..audit import AuditedRoute, record_audit
from ..catalog import get_catalog
from ..context_settings import context_settings
from ..contracts.identity import ROLES, Principal
from ..deps import current_user, get_db
from ..domain.authorization import Target, policy
from ..domain.ledger import MILLI
from ..models import AuditLog, Department, FileObject, MemoryDreamRun, ModelUsage, PlatformSetting, QuotaPolicy, User, UserRole, utcnow
from ..quota import quota_windows
from ..schemas import ContextSettings, UTCModel

router = APIRouter(prefix="/v1/admin", tags=["admin"], route_class=AuditedRoute)
Limit = Query(default=50, ge=1, le=100)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserCreate(Input):
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(max_length=120)
    password: str = Field(min_length=8, max_length=1024)
    dept_id: str | None = Field(default=None, max_length=40)
    roles: list[Literal["user", "dept_admin", "platform_admin", "auditor"]] = Field(default_factory=lambda: ["user"], max_length=4)


class UserPatch(Input):
    display_name: str | None = Field(default=None, max_length=120)
    dept_id: str | None = Field(default=None, max_length=40)
    roles: list[Literal["user", "dept_admin", "platform_admin", "auditor"]] | None = Field(default=None, max_length=4)
    disabled: bool | None = None
    quota_policy_id: str | None = Field(default=None, max_length=40)


class PasswordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=8, max_length=1024)


class DeptCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = Field(default=None, max_length=40)
    quota_policy_id: str | None = Field(default=None, max_length=40)


class DeptPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: str | None = Field(default=None, max_length=40)
    quota_policy_id: str | None = Field(default=None, max_length=40)


class PolicyCreate(Input):
    name: str = Field(min_length=1, max_length=120)
    daily_tokens: int = Field(ge=0, le=2**63 - 1)
    monthly_tokens: int = Field(ge=0, le=2**63 - 1)
    library_bytes: int = Field(ge=0, le=2**63 - 1)
    max_concurrent_runs: int = Field(ge=1, le=1000)
    is_default: bool = False


class PolicyPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    daily_tokens: int | None = Field(default=None, ge=0, le=2**63 - 1)
    monthly_tokens: int | None = Field(default=None, ge=0, le=2**63 - 1)
    library_bytes: int | None = Field(default=None, ge=0, le=2**63 - 1)
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=1000)
    is_default: bool | None = None


class UserOut(UTCModel):
    id: str
    username: str | None
    display_name: str
    email: str
    dept: dict | None
    roles: list[str]
    disabled: bool
    quota_policy: dict | None
    auth: str
    created_at: datetime
    last_login_at: datetime | None
    usage: dict


class AuditOut(UTCModel):
    id: str
    at: datetime
    actor: dict | None
    actor_service: str | None
    action: str
    target_type: str
    target_id: str | None
    outcome: str
    request_id: str
    detail: dict


def _cursor(values: list) -> str:
    return base64.urlsafe_b64encode(json.dumps(values).encode()).decode().rstrip("=")


def _decode(cursor: str | None, *, dated: bool = False):
    if cursor is None:
        return None
    try:
        if len(cursor) > 512:
            raise ValueError
        values = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if not isinstance(values, list) or len(values) != (2 if dated else 1):
            raise ValueError
        if not all(isinstance(v, str) and 0 < len(v) <= 64 for v in values):
            raise ValueError
        return (datetime.fromisoformat(values[0]), values[1]) if dated else values[0]
    except (ValueError, TypeError):
        raise HTTPException(422, "invalid cursor") from None


def _audit(db, user, action, target_type, target_id=None, **kw):
    record_audit(db, action, target_type, target_id, actor_id=user.id, **kw)


def _require(db, user, action, target=None):
    if not policy.can(user.principal, action, target):
        _reject(db, user, action, 403, "forbidden")


def _reject(db, user, action, status, message):
    db.rollback()
    _audit(db, user, action, "admin", outcome="failure", detail={"reason": str(status)})
    db.commit()
    raise HTTPException(status, message)


def _commit(db, user, action, target_type, target_id=None, detail=None):
    _audit(db, user, action, target_type, target_id, detail=detail)
    try:
        db.commit()
    except IntegrityError:
        _reject(db, user, action, 409, "conflict")


def _scoped(principal: Principal, action="users.list") -> bool:
    return "platform_admin" not in principal.roles and not (action == "usage.list" and "auditor" in principal.roles)


def _ref(db, cls, key):
    if key is not None and db.get(cls, key) is None:
        raise HTTPException(422, "unknown reference")


def _brief(row):
    return {"id": row.id, "name": row.name} if row else None


def _usage(db, ids):
    """Each user's weighted use in the current quota windows, from the ledger's frozen amounts."""
    windows = quota_windows()
    (day, _, _), (month, _, _) = windows["daily_tokens"], windows["monthly_tokens"]
    amount = ModelUsage.weighted_amount
    rows = db.execute(
        select(ModelUsage.user_id, func.sum(case((ModelUsage.created_at >= day, amount), else_=0)), func.sum(amount))
        .where(ModelUsage.user_id.in_(ids), ModelUsage.created_at >= min(day, month))
        .group_by(ModelUsage.user_id)
    )
    return {uid: {"daily": round((daily or 0) / MILLI), "monthly": round((monthly or 0) / MILLI)} for uid, daily, monthly in rows}


def _user_out(db, row, usage=None):
    return UserOut(
        id=row.id,
        username=row.username,
        display_name=row.display_name,
        email=row.email,
        dept=_brief(row.department),
        roles=sorted(row.principal.roles),
        disabled=row.disabled_at is not None,
        quota_policy=_brief(row.quota_policy),
        auth="oidc" if row.issuer else "local",
        created_at=row.created_at,
        last_login_at=row.last_login_at,
        usage=usage if usage is not None else _usage(db, [row.id]).get(row.id, {"daily": 0, "monthly": 0}),
    ).model_dump(mode="json")


@router.get("/users")
def list_users(
    q: str = Query(default="", max_length=120),
    dept_id: str | None = None,
    role: str | None = None,
    disabled: bool | None = None,
    cursor: str | None = None,
    limit: int = Limit,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _require(db, user, "users.list")
    stmt = select(User)
    if _scoped(user.principal):
        stmt = stmt.where(User.dept_id == user.dept_id)
    if dept_id is not None:
        stmt = stmt.where(User.dept_id == dept_id)
    if q:
        term = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            User.username.ilike(term, escape="\\") | User.display_name.ilike(term, escape="\\") | User.email.ilike(term, escape="\\")
        )
    if role is not None:
        if role not in ROLES:
            raise HTTPException(422, "invalid role")
        stmt = stmt.where(User.role_rows.any(UserRole.role == role))
    if disabled is not None:
        stmt = stmt.where(User.disabled_at.is_not(None) if disabled else User.disabled_at.is_(None))
    if pos := _decode(cursor, dated=True):
        stmt = stmt.where(tuple_(User.created_at, User.id) < pos)
    rows = list(db.scalars(stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1)))
    counts = _usage(db, [r.id for r in rows[:limit]])
    result = {
        "items": [_user_out(db, r, counts.get(r.id, {"daily": 0, "monthly": 0})) for r in rows[:limit]],
        "next_cursor": _cursor([rows[limit - 1].created_at.isoformat(), rows[limit - 1].id]) if len(rows) > limit else None,
    }
    _commit(db, user, "users.list", "user")
    return result


@router.post("/users", status_code=201)
def add_user(body: UserCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    dept_id = body.dept_id if "dept_id" in body.model_fields_set or not _scoped(user.principal) else user.dept_id
    _require(db, user, "users.create", Target(dept_id=dept_id, new_roles=frozenset(body.roles)))
    _ref(db, Department, dept_id)
    try:
        row = create_user(db, body.username, body.password, display_name=body.display_name)
    except AccountError as exc:
        _reject(db, user, "users.create", 409 if "already" in str(exc) else 422, str(exc))
    row.dept_id = dept_id
    row.role_rows = [UserRole(role=r) for r in sorted(set(body.roles) | {"user"})]
    _commit(db, user, "users.create", "user", row.id, {"roles": body.roles, "dept_id": dept_id})
    db.refresh(row)
    return _user_out(db, row)


def _editable(db, user, target_id, action, body=None):
    _require(db, user, "users.list")
    stmt = select(User).where(User.id == target_id)
    if _scoped(user.principal):
        stmt = stmt.where(User.dept_id == user.dept_id)
    row = db.scalar(stmt.with_for_update(of=User))
    if row is None:
        _reject(db, user, action, 403 if _scoped(user.principal) else 404, "forbidden" if _scoped(user.principal) else "not found")
    _require(
        db,
        user,
        action,
        Target(
            dept_id=row.dept_id,
            roles=row.principal.roles,
            new_roles=frozenset(body.roles) if body and body.roles is not None else None,
            new_dept_id=body.dept_id if body else None,
        ),
    )
    if body and _scoped(user.principal) and "dept_id" in body.model_fields_set and body.dept_id != user.dept_id:
        _reject(db, user, action, 403, "forbidden")
    return row


@router.patch("/users/{target_id}")
def patch_user(target_id: str, body: UserPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _editable(db, user, target_id, "users.update", body)
    changes = body.model_dump(exclude_unset=True)
    if any(changes.get(k, "present") is None for k in ("roles", "display_name", "disabled")):
        raise HTTPException(422, "field cannot be null")
    _ref(db, Department, body.dept_id)
    _ref(db, QuotaPolicy, body.quota_policy_id)
    for field, value in changes.items():
        if field == "roles":
            row.role_rows = [UserRole(role=r) for r in sorted(set(value) | {"user"})]
        elif field == "disabled":
            row.disabled_at = utcnow() if value else None
            if value:
                revoke_sessions(db, row.id)
        else:
            setattr(row, field, value)
    _commit(
        db, user, "users.update", "user", row.id, {"fields": sorted(changes), **{k: v for k, v in changes.items() if k != "display_name"}}
    )
    db.refresh(row)
    return _user_out(db, row)


@router.post("/users/{target_id}/password", status_code=204)
def reset_password(target_id: str, body: PasswordInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _editable(db, user, target_id, "users.password")
    if row.issuer:
        raise HTTPException(409, "OIDC passwords are managed by the identity provider")
    try:
        set_password(db, row, body.password)
    except AccountError as exc:
        _reject(db, user, "users.password", 422, str(exc))
    _commit(db, user, "users.password", "user", row.id)
    return Response(status_code=204)


def _array_page(db, stmt, cls, cursor, limit, response):
    if pos := _decode(cursor):
        stmt = stmt.where(cls.id > pos)
    rows = list(db.scalars(stmt.order_by(cls.id).limit(limit + 1)))
    if len(rows) > limit:
        response.headers["X-Next-Cursor"] = _cursor([rows[limit - 1].id])
    return rows[:limit]


def _dept_out(db, row, members=None):
    return {
        "id": row.id,
        "name": row.name,
        "parent_id": row.parent_id,
        "quota_policy": _brief(row.quota_policy),
        "members": members if members is not None else db.scalar(select(func.count()).select_from(User).where(User.dept_id == row.id)),
    }


@router.get("/departments")
def list_departments(
    response: Response, cursor: str | None = None, limit: int = Limit, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    _require(db, user, "departments.list")
    stmt = select(Department)
    if _scoped(user.principal):
        stmt = stmt.where(Department.id == user.dept_id)
    rows = _array_page(db, stmt, Department, cursor, limit, response)
    counts = dict(db.execute(select(User.dept_id, func.count()).where(User.dept_id.in_([r.id for r in rows])).group_by(User.dept_id)).all())
    result = [_dept_out(db, r, counts.get(r.id, 0)) for r in rows]
    _commit(db, user, "departments.list", "department")
    return result


def _department_refs(db, row, values):
    _ref(db, QuotaPolicy, values.get("quota_policy_id"))
    parent = values.get("parent_id")
    visited = {row.id} if row else set()
    while parent is not None:
        if parent in visited:
            raise HTTPException(409, "department cycle")
        visited.add(parent)
        item = db.get(Department, parent)
        if item is None:
            raise HTTPException(422, "unknown parent department")
        parent = item.parent_id
        if len(visited) > 1000:
            raise HTTPException(409, "department hierarchy too deep")


@router.post("/departments", status_code=201)
def add_department(body: DeptCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require(db, user, "departments.create")
    values = body.model_dump()
    _department_refs(db, None, values)
    row = Department(**values)
    db.add(row)
    db.flush()
    _commit(db, user, "departments.create", "department", row.id)
    return _dept_out(db, row)


@router.patch("/departments/{target_id}")
def patch_department(target_id: str, body: DeptPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require(db, user, "departments.update")
    row = db.scalar(select(Department).where(Department.id == target_id).with_for_update(of=Department))
    if row is None:
        raise HTTPException(404, "not found")
    values = body.model_dump(exclude_unset=True)
    if "name" in values and values["name"] is None:
        raise HTTPException(422, "name cannot be null")
    _department_refs(db, row, values)
    for key, value in values.items():
        setattr(row, key, value)
    _commit(db, user, "departments.update", "department", row.id, {"fields": sorted(values)})
    db.refresh(row)
    return _dept_out(db, row)


@router.delete("/departments/{target_id}", status_code=204)
def delete_department(target_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require(db, user, "departments.delete")
    row = db.scalar(select(Department).where(Department.id == target_id).with_for_update(of=Department))
    if row is None:
        raise HTTPException(404, "not found")
    if db.scalar(select(User.id).where(User.dept_id == target_id).limit(1)) or db.scalar(
        select(Department.id).where(Department.parent_id == target_id).limit(1)
    ):
        _reject(db, user, "departments.delete", 409, "department has members or children")
    db.delete(row)
    _commit(db, user, "departments.delete", "department", target_id)
    return Response(status_code=204)


def _policy_out(db, row, count=None):
    return {
        "id": row.id,
        "name": row.name,
        "daily_tokens": row.daily_tokens,
        "monthly_tokens": row.monthly_tokens,
        "library_bytes": row.library_bytes,
        "max_concurrent_runs": row.max_concurrent_runs,
        "is_default": row.is_default,
        "users": count if count is not None else db.scalar(select(func.count()).select_from(User).where(User.quota_policy_id == row.id)),
    }


@router.get("/quota-policies")
def list_policies(
    response: Response, cursor: str | None = None, limit: int = Limit, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    _require(db, user, "quota-policies.list")
    rows = _array_page(db, select(QuotaPolicy), QuotaPolicy, cursor, limit, response)
    counts_stmt = select(User.quota_policy_id, func.count()).where(User.quota_policy_id.in_([r.id for r in rows]))
    if _scoped(user.principal):
        counts_stmt = counts_stmt.where(User.dept_id == user.dept_id)
    counts = dict(db.execute(counts_stmt.group_by(User.quota_policy_id)).all())
    result = [_policy_out(db, r, counts.get(r.id, 0)) for r in rows]
    _commit(db, user, "quota-policies.list", "quota_policy")
    return result


def _set_default(db, value):
    if value:
        db.execute(update(QuotaPolicy).where(QuotaPolicy.is_default.is_(True)).values(is_default=False))
        db.flush()


@router.post("/quota-policies", status_code=201)
def add_policy(body: PolicyCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require(db, user, "quota-policies.create")
    _set_default(db, body.is_default)
    row = QuotaPolicy(**body.model_dump())
    db.add(row)
    db.flush()
    _commit(db, user, "quota-policies.create", "quota_policy", row.id)
    return _policy_out(db, row)


@router.patch("/quota-policies/{target_id}")
def patch_policy(target_id: str, body: PolicyPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require(db, user, "quota-policies.update")
    row = db.get(QuotaPolicy, target_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, "not found")
    values = body.model_dump(exclude_unset=True)
    if None in values.values():
        raise HTTPException(422, "field cannot be null")
    _set_default(db, values.get("is_default"))
    for key, value in values.items():
        setattr(row, key, value)
    _commit(db, user, "quota-policies.update", "quota_policy", row.id, {"fields": sorted(values)})
    return _policy_out(db, row)


@router.delete("/quota-policies/{target_id}", status_code=204)
def delete_policy(target_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require(db, user, "quota-policies.delete")
    row = db.get(QuotaPolicy, target_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, "not found")
    if (
        row.is_default
        or db.scalar(select(User.id).where(User.quota_policy_id == target_id).limit(1))
        or db.scalar(select(Department.id).where(Department.quota_policy_id == target_id).limit(1))
    ):
        _reject(db, user, "quota-policies.delete", 409, "policy in use or default")
    db.delete(row)
    _commit(db, user, "quota-policies.delete", "quota_policy", target_id)
    return Response(status_code=204)


@router.get("/audit")
def list_audit(
    actor_id: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    before: datetime | None = None,
    after: datetime | None = None,
    cursor: str | None = None,
    limit: int = Limit,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _require(db, user, "audit.list")
    stmt = select(AuditLog, User.display_name).outerjoin(User, User.id == AuditLog.actor_id)
    for column, value in (
        (AuditLog.actor_id, actor_id),
        (AuditLog.action, action),
        (AuditLog.target_type, target_type),
        (AuditLog.target_id, target_id),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    if before is not None:
        stmt = stmt.where(AuditLog.at < before)
    if after is not None:
        stmt = stmt.where(AuditLog.at >= after)
    if pos := _decode(cursor, dated=True):
        stmt = stmt.where(tuple_(AuditLog.at, AuditLog.id) < pos)
    rows = db.execute(stmt.order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit + 1)).all()
    result = {
        "items": [
            AuditOut(
                id=r.id,
                at=r.at,
                actor={"id": r.actor_id, "name": name} if r.actor_id else None,
                actor_service=r.actor_service,
                action=r.action,
                target_type=r.target_type,
                target_id=r.target_id,
                outcome=r.outcome,
                request_id=r.request_id,
                detail=r.detail,
            ).model_dump(mode="json")
            for r, name in rows[:limit]
        ],
        "next_cursor": _cursor([rows[limit - 1][0].at.isoformat(), rows[limit - 1][0].id]) if len(rows) > limit else None,
    }
    _commit(db, user, "audit.list", "audit")
    return result


@router.get("/users/{target_id}/usage")
def admin_user_usage(
    target_id: str,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Per-user token usage, grouped by model; no prompts or answers."""
    _require(db, user, "usage.list")
    target = db.get(User, target_id)
    if target is None:
        raise HTTPException(404, "user not found")
    if _scoped(user.principal, "usage.list") and target.dept_id != user.dept_id:
        raise HTTPException(403, "forbidden")
    end = (to or utcnow()).replace(tzinfo=UTC) if to is None or to.tzinfo is None else to
    start = from_ or end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if start >= end or end - start > timedelta(days=366):
        raise HTTPException(422, "usage range must be between 0 and 366 days")
    rows = db.execute(
        select(
            ModelUsage.model,
            func.sum(ModelUsage.weighted_amount),
            func.sum(ModelUsage.prompt_tokens),
            func.sum(ModelUsage.completion_tokens),
            func.count(),
        )
        .where(ModelUsage.user_id == target.id, ModelUsage.created_at >= start, ModelUsage.created_at < end)
        .group_by(ModelUsage.model)
        .order_by(ModelUsage.model)
    ).all()
    items = [
        {
            "model": model,
            "weighted": round((cost or 0) / MILLI),
            "input_tokens": inputs or 0,
            "output_tokens": outputs or 0,
            "calls": calls,
        }
        for model, cost, inputs, outputs, calls in rows
    ]
    _commit(db, user, "usage.list", "user", target.id, detail={"group_by": "model"})
    return {
        "user": {"id": target.id, "display_name": target.display_name, "username": target.username},
        "from": start,
        "to": end,
        "items": items,
        "total": {
            "weighted": sum(item["weighted"] for item in items),
            "input_tokens": sum(item["input_tokens"] for item in items),
            "output_tokens": sum(item["output_tokens"] for item in items),
            "calls": sum(item["calls"] for item in items),
        },
    }


def _file_provenance(row: FileObject, owner: User | None) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "content_type": row.content_type,
        "size": row.size,
        "sha256": row.sha256,
        "status": row.status,
        "version": row.version,
        "lineage_id": row.lineage_id,
        "project_id": row.project_id,
        "created_at": row.created_at,
        "deleted_at": row.deleted_at,
        "purged": row.purged_at is not None,
        "uploader": {
            "id": owner.id,
            "username": owner.username,
            "display_name": owner.display_name,
        }
        if owner
        else None,
    }


@router.get("/files")
def admin_files(
    q: str = Query(default="", max_length=200),
    limit: int = Limit,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Find who uploaded a file. Metadata only; file content is not returned here."""
    if "platform_admin" not in user.principal.roles:
        raise HTTPException(403, "forbidden")
    stmt = select(FileObject, User).outerjoin(User, User.id == FileObject.user_id)
    needle = q.strip()
    if needle:
        stmt = stmt.where(
            or_(
                FileObject.id == needle,
                FileObject.filename.ilike(f"%{needle}%"),
                FileObject.sha256.startswith(needle),
            )
        )
    rows = db.execute(stmt.order_by(FileObject.created_at.desc(), FileObject.id.desc()).limit(limit)).all()
    _commit(db, user, "files.provenance", "file", detail={"query": bool(needle)})
    return {"items": [_file_provenance(file, owner) for file, owner in rows]}


@router.get("/files/{file_id}")
def admin_file_detail(
    file_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Version history and uploader for one file lineage."""
    if "platform_admin" not in user.principal.roles:
        raise HTTPException(403, "forbidden")
    file = db.get(FileObject, file_id)
    if file is None:
        raise HTTPException(404, "file not found")
    rows = db.execute(
        select(FileObject, User)
        .outerjoin(User, User.id == FileObject.user_id)
        .where(FileObject.lineage_id == file.lineage_id)
        .order_by(FileObject.version.desc())
    ).all()
    _commit(db, user, "files.provenance", "file", file_id)
    return {
        "lineage_id": file.lineage_id,
        "versions": [_file_provenance(item, owner) for item, owner in rows],
    }


@router.get("/usage")
def admin_usage(
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    group_by: Literal["dept", "user", "model"] = "dept",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _require(db, user, "usage.list")
    end = (to or utcnow()).replace(tzinfo=UTC) if to is None or to.tzinfo is None else to
    start = from_ or end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if start >= end or end - start > timedelta(days=366):
        raise HTTPException(422, "usage range must be between 0 and 366 days")
    # The department at the time of the call (snapshot), so moving a user does not move their history.
    dept_column = ModelUsage.dept_id
    key, label = {
        "dept": (dept_column, Department.name),
        "user": (ModelUsage.user_id, User.display_name),
        "model": (ModelUsage.model, ModelUsage.model),
    }[group_by]
    stmt = (
        select(
            key,
            label,
            func.sum(ModelUsage.weighted_amount),
            func.sum(ModelUsage.prompt_tokens),
            func.sum(ModelUsage.completion_tokens),
            func.count(),
        )
        .select_from(ModelUsage)
        # not through runs: the ledger outlives purged conversations
        .outerjoin(User, User.id == ModelUsage.user_id)
        .outerjoin(Department, Department.id == dept_column)
        .where(ModelUsage.created_at >= start, ModelUsage.created_at < end)
    )
    if _scoped(user.principal, "usage.list"):
        stmt = stmt.where(dept_column == user.dept_id)
    rows = db.execute(stmt.group_by(key, label).order_by(key).limit(1001)).all()
    if len(rows) > 1000:
        raise HTTPException(422, "too many usage groups; narrow the date range")
    result = {
        "rows": [
            {
                "key": key or "",
                "label": label or "未分配部门",
                "weighted": round((cost or 0) / MILLI),
                "input_tokens": inputs or 0,
                "output_tokens": outputs or 0,
                "calls": count,
            }
            for key, label, cost, inputs, outputs, count in rows
        ]
    }
    _commit(db, user, "usage.list", "usage", detail={"group_by": group_by})
    return result


@router.get("/memory-dream")
def admin_memory_dream(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Aggregate the nightly background job without exposing memory content."""
    _require(db, user, "usage.list")
    rows = list(db.scalars(select(MemoryDreamRun)).all())
    applied = [r for r in rows if r.status == "applied"]
    tokens = sum(int((r.token_usage or {}).get("prompt_tokens", 0)) + int((r.token_usage or {}).get("completion_tokens", 0)) for r in rows)
    return {
        "runs": len(rows),
        "applied": len(applied),
        "failed": sum(1 for r in rows if r.status == "failed"),
        "skipped": sum(1 for r in rows if r.status == "skipped"),
        "reverted": sum(1 for r in rows if r.status == "reverted"),
        "users": len({r.user_id for r in rows}),
        "changes": sum(len((r.plan or {}).get("changes") or []) for r in applied),
        "tokens": tokens,
    }


def _context_admin(user):
    if "platform_admin" not in user.principal.roles:
        raise HTTPException(403, "仅平台管理员可管理上下文设置")


def _context_out(db):
    return {
        **context_settings(db),
        "models": [{"id": m.id, "name": m.name, "context_tokens": m.context_tokens} for m in get_catalog().available()],
    }


@router.get("/settings/context")
def get_context_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    _context_admin(user)
    out = _context_out(db)
    _commit(db, user, "settings.context.read", "settings", "context")
    return out


@router.put("/settings/context")
def put_context_settings(body: ContextSettings, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _context_admin(user)
    # Serialize all admins on one existing row, including first creation of the setting.
    from ..locks import user_critical_section

    admin_id = db.scalar(select(User.id).order_by(User.id).limit(1))
    with user_critical_section(db, admin_id):
        row = db.get(PlatformSetting, "context", populate_existing=True)
        if row is None:
            row = PlatformSetting(key="context", value=body.model_dump())
            db.add(row)
        else:
            row.value = body.model_dump()
        _commit(
            db,
            user,
            "settings.context.update",
            "settings",
            "context",
            {
                "fields": ["auto_compact", "threshold_ratio"],
                "auto_compact": body.auto_compact,
                "threshold_ratio": str(body.threshold_ratio),
            },
        )
        return _context_out(db)
