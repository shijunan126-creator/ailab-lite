"""In-memory checkpoint endpoints for Agent loop fakes, with the same step CAS and JSON round trip as the API."""

from __future__ import annotations

import asyncio
import json

from ailab_platform.clients import ClientError


class CheckpointFake:
    checkpoint: dict | None = None  # class default; the first write makes it per-instance
    paused: dict | None = None

    async def delivery_receipt(self, run_id, delivery_id):
        return None

    async def get_checkpoint(self, run_id):
        return self.checkpoint

    async def put_checkpoint(self, run_id, payload):
        # The CAS above is a network round trip in production: yielding here lets concurrent coroutines
        # interleave exactly as they do against the API, so a fake cannot hide a lost update.
        await asyncio.sleep(0)
        self.checkpoints = getattr(self, "checkpoints", 0) + 1
        step = self.checkpoint["step"] if self.checkpoint else 0
        if payload["expected_step"] != step:
            raise ClientError("PUT", f"/internal/runs/{run_id}/checkpoint", 409, "stale_step")
        boundary = payload["state"].get("context_boundary") or {}
        previous = (self.checkpoint or {}).get("state", {}).get("context_boundary")
        if boundary and boundary != previous and hasattr(self, "events"):
            self.events.append(("agent.context_boundary", {k: v for k, v in boundary.items() if k not in {"epoch", "run_id"}}))
        self.checkpoint = {"step": step + 1, "state": json.loads(json.dumps(payload["state"]))}
        return {"step": step + 1}

    async def pause_run(self, run_id, payload):
        self.paused = payload
        return {"status": payload["status"]}


def background_request(broker, command):
    import shlex

    path = shlex.split(command)[-1]
    if path.startswith("{"):
        return json.loads(path)
    files = dict(getattr(broker, "put_calls", []))
    files.update(getattr(broker, "files", {}))
    return json.loads(files[path])
