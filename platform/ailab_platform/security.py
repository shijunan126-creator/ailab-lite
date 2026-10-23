"""Token authentication primitives (session tokens from a password login, and API tokens)."""

from __future__ import annotations

import ipaddress

from fastapi import Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from .config import settings
from .db import hash_token
from .models import ApiToken, User, utcnow


def bearer_token(request: Request) -> str | None:
    """The Bearer token from the Authorization header, and nowhere else.

    Query-string tokens are not accepted: URLs end up in browser history, proxy and access
    logs. The Portal keeps the token in an httpOnly cookie and adds the header server-side.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def authenticate(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    row = db.scalar(
        select(ApiToken).where(
            ApiToken.token_hash == hash_token(token),
            ApiToken.revoked_at.is_(None),
            or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > utcnow()),
        )
    )
    if row is None:
        return None
    user = db.get(User, row.user_id, options=[joinedload(User.role_rows)])
    return None if user is None or user.disabled_at is not None else user


def service_token_ok(token: str | None) -> bool:
    from .service_auth import authenticator

    principal = authenticator.authenticate(token)
    return principal is not None and "metrics:read" in principal.scopes


def client_address(request: Request) -> str:
    """Walk trusted hops from the socket peer; a client cannot prepend its own identity."""
    peer = request.client.host if request.client else "unknown"
    networks = [ipaddress.ip_network(value) for value in settings.trusted_proxies]

    def trusted(value: str) -> bool:
        try:
            return any(ipaddress.ip_address(value) in network for network in networks)
        except ValueError:
            return False

    if not trusted(peer):
        return peer
    chain = request.headers.get("x-forwarded-for", "").split(",")
    # Malformed or unreasonably long headers must not create arbitrary throttle buckets.
    if len(chain) > 32:
        return peer
    try:
        hops = [str(ipaddress.ip_address(value.strip())) for value in chain]
    except ValueError:
        return peer
    for hop in reversed(hops):
        if not trusted(peer):
            break
        peer = hop
    return peer
