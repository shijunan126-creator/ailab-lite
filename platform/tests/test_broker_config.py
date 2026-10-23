import pytest
from pydantic import ValidationError

from ailab_platform.config import Settings


@pytest.mark.parametrize(
    "overrides",
    [
        {"broker_owner": "bad:owner"},
        {"sandbox_ttl_seconds": 0},
        {"sandbox_ttl_seconds": 20, "sandbox_max_lifetime_seconds": 10},
        {"max_sandboxes": 1, "sandbox_render_reserved_slots": 1},
        {"sandbox_render_memory_bytes": 4 * 1024**3},
        {"sandbox_queue_limit": 0},
        {"sandbox_max_per_user": 0},
        {"broker_lock_ttl_seconds": 0},
    ],
)
def test_broker_rejects_invalid_capacity_and_lease_limits(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)
