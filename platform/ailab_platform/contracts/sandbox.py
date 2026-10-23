"""Resource specifications and opaque lease handles; no provider dependencies."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class SandboxSpec:
    image: str
    profile: str = "standard"
    cpu: float = 1
    memory: int = 1536 * 1024**2
    disk: int = 4 * 1024**3
    ttl: int = 1200
    max_lifetime: int = 4 * 3600

    def __post_init__(self) -> None:
        if not self.image or self.profile not in {"standard", "render"}:
            raise ValueError("invalid sandbox image or profile")
        if (
            not isfinite(self.cpu)
            or min(self.cpu, self.memory, self.disk, self.ttl, self.max_lifetime) <= 0
            or self.ttl > self.max_lifetime
        ):
            raise ValueError("sandbox limits must be positive and ttl <= max_lifetime")


@dataclass(frozen=True)
class LeaseHandle:
    lease_id: str
    provider_ref: str


@dataclass(frozen=True)
class SandboxResource:
    provider_ref: str
    labels: dict[str, str]
    running: bool = True
