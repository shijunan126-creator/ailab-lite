"""Accounts: create users, set passwords, log in for a session token."""

from __future__ import annotations

import re
import secrets
import threading
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_audit
from .config import settings
from .db import hash_token
from .models import ApiToken, User, UserRole, new_id, utcnow
from .passwords import DUMMY_HASH, check_policy, hash_password, verify_password

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$")


class AccountError(ValueError):
    """The request is wrong in a way the caller can fix (message is safe to show)."""


def normalize_username(raw: str) -> str:
    return raw.strip().lower()


def create_user(db: Session, username: str, password: str, *, display_name: str = "", email: str = "") -> User:
    username = normalize_username(username)
    if not USERNAME_RE.match(username):
        raise AccountError("username: 3-64 characters, lowercase letters, digits, . _ - (not at either end)")
    if problem := check_policy(password):
        raise AccountError(problem)
    email = email.strip().lower() or f"{username}@ailab.local"
    if db.scalar(select(User).where(User.username == username)):
        raise AccountError(f"user {username!r} already exists")
    if db.scalar(select(User).where(User.email == email)):
        raise AccountError(f"email {email!r} is already used")
    user = User(
        id=new_id("usr"),
        email=email,
        username=username,
        display_name=display_name.strip() or username,
        password_hash=hash_password(password),
    )
    user.role_rows = [UserRole(role="user")]
    db.add(user)
    db.flush()
    return user


def set_password(db: Session, user: User, password: str) -> None:
    """New password; every session the user has is ended (a stolen session must not survive a reset)."""
    if problem := check_policy(password):
        raise AccountError(problem)
    user.password_hash = hash_password(password)
    revoke_sessions(db, user.id)


def revoke_sessions(db: Session, user_id: str) -> int:
    now = utcnow()
    rows = list(
        db.scalars(select(ApiToken).where(ApiToken.user_id == user_id, ApiToken.name == SESSION_NAME, ApiToken.revoked_at.is_(None)))
    )
    for row in rows:
        row.revoked_at = now
    return len(rows)


SESSION_NAME = "session"


def issue_session(db: Session, user: User) -> tuple[str, int]:
    """(raw token, lifetime in seconds). Only the token's hash is stored."""
    token = "ses_" + secrets.token_urlsafe(32)
    lifetime = settings.session_ttl_seconds
    db.add(
        ApiToken(
            id=new_id("tok"),
            user_id=user.id,
            name=SESSION_NAME,
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(seconds=lifetime),
        )
    )
    # Expired sessions are dead weight; clearing the user's own on every login keeps the table small.
    for old in db.scalars(
        select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.name == SESSION_NAME, ApiToken.expires_at < utcnow())
    ):
        db.delete(old)
    return token, lifetime


class LoginThrottle:
    """Too many failed logins for one username (or from one address) pause further attempts.

    In memory and per process, which is enough to make online guessing impractical on a single API; with
    several replicas each keeps its own count, so the effective limit is per replica.
    """

    def __init__(self, max_failures: int, window_seconds: int) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def retry_after(self, *keys: str) -> int:
        """Seconds until one of the keys may try again, 0 if none is blocked."""
        now = time.monotonic()
        with self._lock:
            wait = 0
            for key in keys:
                recent = self._recent(key, now)
                if len(recent) >= self.max_failures:
                    wait = max(wait, int(self.window - (now - recent[0])) + 1)
            return wait

    def fail(self, *keys: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in keys:
                self._recent(key, now)
                self._failures.setdefault(key, []).append(now)
            if len(self._failures) > 10_000:  # a flood of distinct names must not grow this without bound
                for key in list(self._failures):
                    self._recent(key, now)

    def succeed(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)


user_throttle = LoginThrottle(settings.login_max_failures, settings.login_window_seconds)
address_throttle = LoginThrottle(settings.login_max_failures * 4, settings.login_window_seconds)  # one address, many names


def check_credentials(db: Session, username: str, password: str) -> User | None:
    """The user if username and password are right and the account is enabled, else None.

    Always spends one password verification, so timing does not tell whether the username exists.
    """
    user = db.scalar(select(User).where(User.username == normalize_username(username)))
    ok = verify_password(password, user.password_hash if user else DUMMY_HASH)
    if user is None or not ok or user.disabled_at is not None:
        return None
    return user


def ensure_bootstrap_admin(db: Session) -> None:
    """Create the first account from AILAB_ADMIN_USERNAME / AILAB_ADMIN_PASSWORD, if it does not exist yet.

    Only creates: changing the variable later does not overwrite a password someone has since chosen
    (use `python -m ailab_platform.users passwd`).
    """
    username, password = settings.admin_username, settings.admin_password
    if not username or not password:
        return
    user = db.scalar(select(User).where(User.username == normalize_username(username)))
    if user is None:
        user = create_user(db, username, password, display_name="Administrator")
        record_audit(db, "users.create", "user", user.id, actor_service="bootstrap")
    if "platform_admin" not in user.principal.roles:
        user.role_rows.append(UserRole(role="platform_admin"))
        record_audit(db, "users.update", "user", user.id, actor_service="bootstrap", detail={"roles": ["platform_admin"]})


class LocalIdentityProvider:
    def __init__(self, db: Session):
        self.db = db

    async def authenticate(self, credentials: dict[str, str]):
        from starlette.concurrency import run_in_threadpool

        from .ports.identity import Identity, IdentityError

        user = await run_in_threadpool(check_credentials, self.db, credentials.get("username", ""), credentials.get("password", ""))
        if user is None:
            raise IdentityError("invalid username or password")
        return Identity(None, user.id, user_id=user.id)


def oidc_user(db: Session, identity) -> User:
    """Only (issuer, subject) links accounts; never auto-link by email or preferred username."""
    from .models import Department
    from .ports.identity import IdentityError

    user = db.scalar(select(User).where(User.issuer == identity.issuer, User.external_subject == identity.subject).with_for_update(of=User))
    if user is not None and user.disabled_at is not None:
        raise IdentityError("disabled account")
    groups = identity.claims
    for part in settings.oidc_groups_claim.split("."):
        groups = groups.get(part, {}) if isinstance(groups, dict) else {}
    if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
        groups = []
    roles = {"user"}
    departments = set()
    for group in groups:
        roles.update(settings.oidc_role_mapping.get(group, []))
        if dept_id := settings.oidc_department_mapping.get(group):
            departments.add(dept_id)
    if len(departments) > 1:
        raise IdentityError("ambiguous department mapping")
    dept_id = next(iter(departments), None)
    if dept_id and db.get(Department, dept_id) is None:
        raise IdentityError("unknown mapped department")
    new = user is None
    if new:
        uid = new_id("usr")
        # A synthetic unique email avoids linking identities across issuers on an unverified email claim.
        user = User(
            id=uid,
            email=f"{uid}@oidc.ailab.local",
            issuer=identity.issuer,
            external_subject=identity.subject,
            display_name=str(identity.claims.get("name") or identity.claims.get("preferred_username") or "用户")[:120],
        )
        db.add(user)
    old_roles = user.principal.roles if not new else frozenset()
    old_dept = user.dept_id
    user.role_rows = [UserRole(role=role) for role in sorted(roles)]
    user.dept_id = dept_id
    db.flush()
    if new:
        record_audit(db, "auth.register", "user", user.id, actor_id=user.id, detail={"auth": "oidc"})
    if old_roles != frozenset(roles) or old_dept != dept_id:
        record_audit(db, "users.update", "user", user.id, actor_service="oidc-mapping", detail={"roles": sorted(roles), "dept_id": dept_id})
    return user
