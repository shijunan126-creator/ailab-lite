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
