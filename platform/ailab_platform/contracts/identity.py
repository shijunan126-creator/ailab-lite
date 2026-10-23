"""Identity contract independent of login mechanism and persistence."""

from dataclasses import dataclass

ROLES = frozenset({"user", "dept_admin", "platform_admin", "auditor"})


@dataclass(frozen=True)
class Principal:
    user_id: str
    issuer: str | None = None
    external_subject: str | None = None
    dept_id: str | None = None
    roles: frozenset[str] = frozenset({"user"})
