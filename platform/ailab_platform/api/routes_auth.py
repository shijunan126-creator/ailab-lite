"""Sign-up, password login, logout and "who am I"."""

from __future__ import annotations

import hmac
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..accounts import (
    AccountError,
    LocalIdentityProvider,
    LoginThrottle,
    address_throttle,
    check_credentials,
    create_user,
    issue_session,
    normalize_username,
    oidc_user,
    set_password,
    user_throttle,
)
from ..adapters.oidc import oidc_provider
from ..audit import AuditedRoute, record_audit
from ..config import settings
from ..db import hash_token
from ..deps import current_user, get_db
from ..domain.authorization import policy
from ..models import ApiToken, OIDCFlow, User, utcnow
from ..passwords import MAX_PASSWORD_LENGTH
from ..ports.identity import IdentityError
from ..schemas import LoginRequest, PasswordChange, RegisterRequest
from ..security import bearer_token, client_address

router = APIRouter(prefix="/v1/auth", tags=["auth"], route_class=AuditedRoute)
log = logging.getLogger("ailab.api.auth")


def _user_out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "roles": sorted(user.principal.roles),
        "dept": {"id": user.department.id, "name": user.department.name} if user.department else None,
        "can_admin": any(policy.can(user.principal, a) for a in ("users.list", "audit.list", "usage.list")),
    }


def _client_address(request: Request) -> str:
    return client_address(request)


# Sign-ups per client address and hour (counted whether they succeed or not: a bot gets nowhere either way).
signup_throttle = LoginThrottle(settings.registrations_per_hour, 3600)


def registration_mode() -> str:
    if settings.auth_backend == "oidc":
        return "closed"
    mode = settings.registration if settings.registration in ("open", "invite", "closed") else "closed"
    if settings.profile == "secure" and mode == "open":
        mode = "invite"
    if mode == "invite" and not settings.invite_code:
        return "closed"  # an invite mode nobody can satisfy
    return mode


@router.get("/options")
def auth_options() -> dict:
    """What the login page should offer. No authentication: it is shown before anyone logs in."""
    return {
        "registration": registration_mode(),
        "oidc": {"enabled": settings.auth_backend in {"oidc", "both"}, "label": settings.oidc_label},
        "password": settings.auth_backend in {"local", "both"},
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Create an account and log it in. Errors are in Chinese: the Portal shows them as they are."""
    mode = registration_mode()
    if mode == "closed":
        raise HTTPException(status_code=403, detail="本平台未开放注册，请联系管理员开通账号")
    if mode == "invite" and not hmac.compare_digest((body.invite_code or "").strip().encode(), settings.invite_code.encode()):
        raise HTTPException(status_code=403, detail="邀请码不正确")
    address = _client_address(request)
    wait = signup_throttle.retry_after(address)
    if wait:
        raise HTTPException(status_code=429, detail=f"注册太频繁，请 {max(1, wait // 60)} 分钟后再试", headers={"Retry-After": str(wait)})
    signup_throttle.fail(address)
    try:
        user = create_user(db, body.username, body.password, display_name=body.display_name or "")
    except AccountError as exc:
        db.rollback()
        text = str(exc)
        if "already exists" in text or "already used" in text:
            raise HTTPException(status_code=409, detail="这个用户名已经被注册了") from exc
        if text.startswith("username"):
            raise HTTPException(status_code=422, detail="用户名为 3～64 位小写字母、数字或 . _ -，且不能以符号开头或结尾") from exc
        if "at least" in text:
            raise HTTPException(status_code=422, detail="密码至少 8 个字符") from exc
        raise HTTPException(status_code=422, detail=text) from exc
    token, lifetime = issue_session(db, user)
    user.last_login_at = utcnow()
    record_audit(db, "auth.register", "user", user.id, actor_id=user.id, detail={"auth": "local"})
    record_audit(db, "auth.login", "user", user.id, actor_id=user.id, detail={"auth": "local"})
    db.commit()
    log.info("registered user=%s from=%s", user.username, address)
    return {"token": token, "expires_in": lifetime, "user": _user_out(user)}


@router.post("/login")
async def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    if settings.auth_backend == "oidc":
        record_audit(db, "auth.login", "user", outcome="failure", detail={"auth": "local", "reason": "backend disabled"})
        db.commit()
        raise HTTPException(403, "local login disabled")
    username = normalize_username(body.username)
    address = _client_address(request)
    wait = max(user_throttle.retry_after(username), address_throttle.retry_after(address))
    if wait:
        record_audit(db, "auth.login", "user", outcome="failure", detail={"auth": "local", "reason": "throttled", "username": username})
        db.commit()
        log.warning("login throttled user=%s from=%s retry_after=%ss", username, address, wait)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"too many failed attempts, try again in {wait} seconds",
            headers={"Retry-After": str(wait)},
        )
    try:
        identity = await LocalIdentityProvider(db).authenticate({"username": username, "password": body.password})
        user = db.get(User, identity.user_id)
    except IdentityError:
        user = None
    if user is None:
        record_audit(db, "auth.login", "user", outcome="failure", detail={"auth": "local", "username": username})
        db.commit()
        user_throttle.fail(username)
        address_throttle.fail(address)
        log.warning("login failed user=%s from=%s", username, address)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",  # the same words whether the name exists or not
        )
    user_throttle.succeed(username)
    token, lifetime = issue_session(db, user)
    user.last_login_at = utcnow()
    record_audit(db, "auth.login", "user", user.id, actor_id=user.id, detail={"auth": "local"})
    db.commit()
    log.info("login ok user=%s from=%s", username, address)
    return {"token": token, "expires_in": lifetime, "user": _user_out(user)}


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return {**_user_out(user), "features": {"genui_html": settings.genui_html_enabled and settings.genui_enabled}}


@router.post("/logout")
def logout(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """End *this* session. (Tokens that are not sessions - the static API token - are left alone.)"""
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(bearer_token(request) or ""), ApiToken.user_id == user.id))
    if row is not None and row.name == "session" and row.revoked_at is None:
        row.revoked_at = utcnow()
    record_audit(db, "auth.logout", "user", user.id, actor_id=user.id)
    db.commit()
    return {"ok": True}


@router.post("/password")
def change_password(body: PasswordChange, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if user.issuer or not user.username:
        raise HTTPException(status_code=400, detail="this account has no password login")
    if len(body.current_password) > MAX_PASSWORD_LENGTH or check_credentials(db, user.username, body.current_password) is None:
        raise HTTPException(status_code=403, detail="current password is wrong")
    try:
        set_password(db, user, body.new_password)  # also ends every session, this one included
    except AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_audit(db, "users.password", "user", user.id, actor_id=user.id)
    db.commit()
    return {"ok": True}


class OIDCCallback(BaseModel):
    state: str = Field(min_length=32, max_length=128)
    code: str = Field(default="", max_length=8192)
    flow_token: str = Field(min_length=32, max_length=128)
    error: str | None = Field(default=None, max_length=120)


def _oidc_enabled():
    if settings.auth_backend not in {"oidc", "both"}:
        raise HTTPException(404, "OIDC disabled")


@router.post("/oidc/start")
async def oidc_start(request: Request, db: Session = Depends(get_db)):
    _oidc_enabled()
    address = _client_address(request)
    if wait := address_throttle.retry_after("oidc:" + address):
        raise HTTPException(429, "too many authentication requests", headers={"Retry-After": str(wait)})
    address_throttle.fail("oidc:" + address)
    state, binding, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
    try:
        url = await oidc_provider.authorization_url(state, nonce, verifier)
    except IdentityError:
        raise HTTPException(502, "identity provider unavailable") from None
    db.execute(delete(OIDCFlow).where(OIDCFlow.expires_at < utcnow()))
    db.add(
        OIDCFlow(
            state_hash=hash_token(state),
            binding_hash=hash_token(binding),
            nonce=nonce,
            verifier=verifier,
            expires_at=utcnow() + timedelta(seconds=settings.oidc_flow_ttl_seconds),
        )
    )
    db.commit()
    return {"authorization_url": url, "flow_token": binding, "expires_in": settings.oidc_flow_ttl_seconds}


@router.post("/oidc/callback")
async def oidc_callback(body: OIDCCallback, db: Session = Depends(get_db)):
    _oidc_enabled()
    # Atomic DELETE consumes the transaction once, before the network exchange. Another callback cannot reuse it.
    flow = db.execute(
        delete(OIDCFlow)
        .where(
            OIDCFlow.state_hash == hash_token(body.state),
            OIDCFlow.binding_hash == hash_token(body.flow_token),
            OIDCFlow.expires_at > utcnow(),
        )
        .returning(OIDCFlow.verifier, OIDCFlow.nonce)
    ).first()
    db.commit()
    try:
        if flow is None or body.error or not body.code:
            raise IdentityError("invalid state or identity provider refusal")
        identity = await oidc_provider.authenticate({"code": body.code, "verifier": flow.verifier, "nonce": flow.nonce})
        user = oidc_user(db, identity)
        token, lifetime = issue_session(db, user)
        user.last_login_at = utcnow()
        record_audit(db, "auth.login", "user", user.id, actor_id=user.id, detail={"auth": "oidc"})
        db.commit()
        return {"token": token, "expires_in": lifetime, "user": _user_out(user)}
    except (IdentityError, IntegrityError):
        db.rollback()
        record_audit(db, "auth.login", "user", outcome="failure", detail={"auth": "oidc", "reason": "authentication refused"})
        db.commit()
        raise HTTPException(401, "OIDC authentication refused") from None
