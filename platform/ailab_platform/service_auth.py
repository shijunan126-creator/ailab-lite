"""Service identities and the endpoint permission matrix. No database or runtime access."""

from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.routing import APIRoute

from .config import settings
from .ports.service_auth import ServiceAuthenticator, ServicePrincipal

SERVICE_SCOPES: dict[str, frozenset[str]] = {
    "api": frozenset({"agent:dispatch", "worker:dispatch", "sandbox:view_workspace"}),
    "agent": frozenset(
        {
            "jobs:run",
            "runs:read",
            "runs:client_calls",
            "runs:status",
            "runs:steer",
            "runs:events",
            "usage:reserve",
            "usage:write",
            "files:read_for_run",
            "sandbox:use",
            "skills:save_for_run",
            "memory:write_for_run",
            "memory:recall_for_run",
            "schedules:write_for_run",
        }
    ),
    "broker": frozenset(
        {
            "runs:read",
            "runs:events",
            "files:read_for_run",
            "files:read_for_preview",
            "files:read_for_document",
            "artifacts:commit_for_run",
            "ocr:workspace",
            "files:derived_write",
        }
    ),
    "worker": frozenset(
        {
            "jobs:file",
            "files:read_pending",
            "files:patch_processing",
            "files:preview_write",
            "files:ocr",
            "sandbox:render",
            "files:derived_write",
        }
    ),
    "ops": frozenset({"metrics:read", "sandbox:inspect", "files:restore_tombstones"}),
}
ALL_SCOPES = frozenset().union(*SERVICE_SCOPES.values())


class CredentialAuthenticator:
    def authenticate(self, token: str | None) -> ServicePrincipal | None:
        if not token:
            return None
        digest = "sha256:" + hashlib.sha256(token.encode()).hexdigest()
        matched = None
        # Evaluate every entry, including after a match; neither tokens nor user ids are logged.
        for name, verifier in settings.service_credentials.items():
            if hmac.compare_digest(digest, verifier):
                matched = ServicePrincipal(name, SERVICE_SCOPES[name])
        if matched:
            return matched
        if settings.profile != "secure" and settings.service_token and hmac.compare_digest(token.encode(), settings.service_token.encode()):
            return ServicePrincipal("legacy", ALL_SCOPES)
        return None


authenticator: ServiceAuthenticator = CredentialAuthenticator()


def bearer_token(request: Request) -> str | None:
    """The Bearer token from the Authorization header, and nowhere else.

    Query-string tokens are not accepted: URLs end up in browser history, proxy and access
    logs. The Portal keeps the token in an httpOnly cookie and adds the header server-side.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def _metrics_reader(token: str | None) -> bool:
    principal = authenticator.authenticate(token)
    return principal is not None and "metrics:read" in principal.scopes


def metrics_auth(request: Request, x_service_token: str | None = Header(default=None)) -> None:
    """/metrics shows traffic and queue sizes: guard it with the service token, in either of the two forms.
    Lives here, not in deps, so the Agent, Broker and Worker never import the database layer."""
    if not (_metrics_reader(x_service_token) or _metrics_reader(bearer_token(request))):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="service token required")


@lru_cache
def require_scope(*scopes: str):
    """Any of these scopes is sufficient; handlers still enforce their resource context."""
    if not scopes or not set(scopes) <= ALL_SCOPES:
        raise ValueError(f"unknown or empty service scope: {scopes}")

    def dependency(x_service_token: str | None = Header(default=None)) -> ServicePrincipal:
        principal = authenticator.authenticate(x_service_token)
        if principal is None or not principal.scopes.intersection(scopes):
            raise HTTPException(status_code=403, detail="service scope required")
        return principal

    dependency.service_scopes = frozenset(scopes)
    return dependency


def assert_internal_scopes(app: FastAPI) -> None:
    def scoped(dependant):
        return bool(getattr(dependant.call, "service_scopes", None)) or any(scoped(child) for child in dependant.dependencies)

    # FastAPI 0.156 keeps include_router lazy. Inspect effective contexts, which
    # include prefixes and inherited dependencies, rather than silently skipping them.
    routes = []
    for route in app.routes:
        if hasattr(route, "effective_route_contexts"):
            routes.extend(route.effective_route_contexts())
        elif isinstance(route, APIRoute):
            routes.append(route)
    missing = [route.path for route in routes if route.path.startswith("/internal") and not scoped(route.dependant)]
    assert not missing, f"internal routes without scope dependencies: {missing}"
