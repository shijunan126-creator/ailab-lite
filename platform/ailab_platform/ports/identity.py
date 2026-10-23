"""Verified identity from a login adapter; upstream credentials never leave the API."""

from dataclasses import dataclass, field
from typing import Protocol


class IdentityError(ValueError):
    """Authentication refused; never include upstream responses or credentials in this message."""


@dataclass(frozen=True)
class Identity:
    issuer: str | None
    subject: str
    claims: dict = field(default_factory=dict)
    user_id: str | None = None


class IdentityProvider(Protocol):
    async def authenticate(self, credentials: dict[str, str]) -> Identity: ...
