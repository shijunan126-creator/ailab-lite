"""Service identity port; a future projected-SA TokenReview adapter implements this."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ServicePrincipal:
    service: str
    scopes: frozenset[str]


@dataclass(frozen=True)
class RunContext:
    """Resolved by the API, never populated with a caller-supplied user id."""

    run_id: str
    user_id: str
    conversation_id: str | None


class ServiceAuthenticator(Protocol):
    def authenticate(self, token: str | None) -> ServicePrincipal | None: ...
