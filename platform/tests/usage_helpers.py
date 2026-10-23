"""Exercise the public service protocol when a test needs settled usage."""

from uuid import uuid4

SERVICE = {"X-Service-Token": "test-service"}


def record_usage(api, run_id, model, prompt_tokens=0, completion_tokens=0, cached_tokens=0):
    call_id = uuid4().hex
    run = api.get(f"/internal/runs/{run_id}", headers=SERVICE).json()
    reserved = api.post(
        "/internal/usage/reserve",
        headers=SERVICE,
        json={
            "call_id": call_id,
            "run_id": run_id,
            "attempt_id": "test",
            "alias": model,
            "mode": (run.get("options") or {}).get("mode", "work"),
            "estimated_input_tokens": max(0, prompt_tokens),
            "max_output_tokens": max(1, completion_tokens),
        },
    )
    assert reserved.status_code == 200, reserved.text
    settled = api.post(
        "/internal/usage/settle",
        headers=SERVICE,
        json={
            "call_id": call_id,
            "actual_backend": model,
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "status": "settled",
        },
    )
    assert settled.status_code == 200, settled.text
    return settled
