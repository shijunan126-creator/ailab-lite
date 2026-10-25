"""In-memory checkpoint endpoints for Agent loop fakes, with the same step CAS and JSON round trip as the API."""

from __future__ import annotations

import json

from ailab_platform.clients import ClientError


class CheckpointFake:
    checkpoint: dict | None = None  # class default; the first write makes it per-instance
    paused: dict | None = None

    async def get_checkpoint(self, run_id):
        return self.checkpoint

    async def put_checkpoint(self, run_id, payload):
        step = self.checkpoint["step"] if self.checkpoint else 0
        if payload["expected_step"] != step:
            raise ClientError("PUT", f"/internal/runs/{run_id}/checkpoint", 409, "stale_step")
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
