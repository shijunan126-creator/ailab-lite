"""Service identities and the endpoint permission matrix. No database or runtime access."""

from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache

from fastapi import FastAPI, Header, HTTPException
from fastapi.routing import APIRoute

from .config import settings
from .ports.service_auth import ServiceAuthenticator, ServicePrincipal

SERVICE_SCOPES: dict[str, frozenset[str]] = {
    "api": frozenset({"agent:dispatch", "worker:dispatch"}),
    "agent": frozenset({"runs:read", "runs:status", "runs:events", "usage:reserve", "usage:write", "files:read_for_run", "sandbox:use"}),
    "broker": frozenset({"runs:read", "runs:events", "files:read_for_run", "artifacts:commit_for_run"}),
    "worker": frozenset({"files:read_pending", "files:patch_processing", "files:preview_write", "sandbox:render"}),
    "ops": frozenset({"metrics:read", "sandbox:inspect"}),
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
