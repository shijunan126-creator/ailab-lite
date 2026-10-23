"""Claim a run's durable job the way the Agent does, so tests can write internal state as the owning attempt."""

from sqlalchemy import select

from ailab_platform.db import SessionLocal
from ailab_platform.models import RunAttempt

SERVICE = {"X-Service-Token": "test-service"}


def owned_headers(api, run_id, consumer="test"):
    """Claim the job. The token is the attempt every internal business write must carry (X-Run-Attempt)."""
    claim = api.post(f"/internal/jobs/{run_id}/claim", headers=SERVICE, json={"consumer": consumer}).json()
    assert claim["state"] == "claimed", claim
    return {**SERVICE, "X-Run-Attempt": claim["attempt_id"]}


def attempt_headers(api, run_id):
    """Headers of the attempt that currently owns the run; claims the job first if nothing owns it yet."""
    with SessionLocal() as db:
        attempt = db.scalar(select(RunAttempt.id).where(RunAttempt.run_id == run_id, RunAttempt.ended_at.is_(None)))
    return owned_headers(api, run_id) if attempt is None else {**SERVICE, "X-Run-Attempt": attempt}


def delivery_headers(headers, run_id, staging_key):
    """A delivery is identified by the staged object: retrying the same object returns what the first commit recorded."""
    return {**headers, "X-Delivery-Id": f"{run_id}:{staging_key}"}
