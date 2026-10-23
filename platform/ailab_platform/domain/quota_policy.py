"""Resolve the most specific complete policy; persistence and settings live on the input adapter."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Limits:
    daily_tokens: int
    monthly_tokens: int
    library_bytes: int
    max_concurrent_runs: int


class PolicyUser(Protocol):
    quota_policy: object | None
    department: object | None
    default_quota_policy: object | None
    quota_fallback: Limits


def limits_for(user: PolicyUser) -> Limits:
    selected = user.quota_policy
    if selected is None and user.department is not None:
        selected = user.department.quota_policy
    if selected is None:
        selected = user.default_quota_policy
    if selected is None:
        return user.quota_fallback
    return Limits(selected.daily_tokens, selected.monthly_tokens, selected.library_bytes, selected.max_concurrent_runs)
