"""Synthetic job fixtures using item deltas, including pre-migration scenarios."""


def make_dream(**kwargs):
    from ailab_platform.models import MemoryDreamRun

    before = kwargs.pop("before_memory", {})
    after = kwargs.pop("after_memory", {})
    job = MemoryDreamRun(**kwargs)
    left = {e["id"]: e for e in before.get("entries", [])}
    right = {e["id"]: e for e in after.get("entries", [])}
    job.change_set = {
        "revision": after.get("revision"),
        "items": [{"id": k, "before": left.get(k), "after": right.get(k)} for k in sorted(left.keys() | right.keys())],
    }
    return job


def snapshot(job, side):
    return {"entries": [c[side] for c in job.change_set.get("items", []) if c.get(side)]}


def install_plan_builder(monkeypatch, builder):
    """Port synthetic plan fixtures to the single document wire protocol (no second model stage)."""
    import json
    from datetime import datetime

    from ailab_platform.memory_dream import service
    from ailab_platform.models import MemoryEpisode

    async def chat(messages, model, user):
        episodes = [
            MemoryEpisode(
                id=e["id"],
                run_id=e["run_id"],
                observed_at=datetime.fromisoformat(e["observed_at"]),
                digest=json.dumps({"user_said": e["user_said"], "assistant_replied": e["assistant_replied"]}),
            )
            for e in json.loads(messages[1]["content"])
        ]
        facts, usage, _ = await builder(episodes, [], user)
        ops = [
            {
                "op": "add",
                "kind": {"user": "preference", "memory": "fact"}.get(f.get("kind"), f.get("kind", "fact")),
                "content": f["text"],
                "run": (f.get("evidence_run_ids") or [None])[0],
                "quote": f.get("source_quote", ""),
            }
            for f in facts
        ]
        return json.dumps({"ops": ops}), usage

    monkeypatch.setattr(service, "_chat", chat)
