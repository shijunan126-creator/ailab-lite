"""Revision-fenced, composable item deltas for background job undo."""

import hashlib
import json

from ..memory_recall.policy import model_view


def scope_entries(user, project_id):
    from ..api.routes_memory import memory_for

    return [
        e
        for e in model_view({**memory_for(user), "enabled": True}, project_id, mode="isolated")["entries"]
        if e.get("project_id") == project_id
    ]


def digest(entries):
    return hashlib.sha256(json.dumps(sorted((e["id"], e["content"]) for e in entries), ensure_ascii=False).encode()).hexdigest()


def record(run, before, after):
    """Coalesce stages without keeping unrelated account items or resurrecting deletions."""
    existing = {c["id"]: c for c in (run.change_set or {}).get("items", [])}
    left = {e["id"]: e for e in before["entries"]}
    right = {e["id"]: e for e in after["entries"]}
    for key in left.keys() | right.keys():
        if left.get(key) == right.get(key):
            continue
        original = existing.get(key, {}).get("before", left.get(key))
        existing[key] = {"id": key, "before": original, "after": right.get(key)}
    run.change_set = {
        "revision": after["revision"],
        "items": [existing[k] for k in sorted(existing) if existing[k]["before"] != existing[k]["after"]],
    }


def redact(value, rewrite):
    if not value or "items" not in value:
        return value or {}
    items = []
    for change in value["items"]:
        before = rewrite(change["before"]) if change.get("before") else None
        after = rewrite(change["after"]) if change.get("after") else None
        if before is not None or after is not None:
            items.append({"id": change["id"], "before": before, "after": after})
    return {**(value or {}), "items": items}
