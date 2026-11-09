"""The complete scope document, with deterministic rendering and no clock."""

from .guard import model_text

SECTIONS = {"fact": "关于你", "preference": "偏好", "project": "在做的事", "interest": "关注的话题", "reference": "资料"}


def kind(entry):
    return {"user": "preference", "memory": "fact"}.get(entry.get("kind"), entry.get("kind", "fact"))


def active(entries):
    return [
        e
        for e in entries
        if e.get("status", "active") == "active" and not e.get("suppressed") and not e.get("invalid_at") and not e.get("superseded_by")
    ]


def render(entries):
    lines = []
    for section, title in SECTIONS.items():
        items = sorted((e for e in active(entries) if kind(e) == section), key=lambda e: e["id"])
        if items:
            lines.append("## " + title)
        for e in items:
            date = str(e.get("observed_at") or e.get("valid_from") or e.get("updated_at") or "")[:10]
            lines.append(f"- [{model_text(e['id'])}] [{date}] [{model_text(e.get('origin') or 'manual')}] {model_text(e['content'])}")
    return "\n".join(lines)


def used_chars(entries):
    return len(render(entries))


def write_result(before, after):
    """A successful write appends only its delta; the conversation already contains the document."""
    left = {e["id"]: e for e in before.get("entries", [])}
    right = {e["id"]: e for e in after.get("entries", [])}
    fields = ("id", "project_id", "kind", "origin", "content", "observed_at", "updated_at", "source", "evidence_run_ids")
    changed = [e for key, e in sorted(right.items()) if key not in left or any(e.get(f) != left[key].get(f) for f in fields)]
    return {
        "memory_delta": True,
        "revision": after["revision"],
        "entries": [{key: e[key] for key in fields if key in e} for e in changed],
        "removed_ids": sorted(left.keys() - right.keys()),
    }


def maintenance_needed(entries, user=None):
    """Protected content cannot be shrunk by a maintenance-only call."""
    from hashlib import sha256

    from ..config import settings

    signature = sha256(render(entries).encode()).hexdigest()
    if used_chars(entries) <= settings.memory_doc_max_chars:
        return False
    protected = [e for e in active(entries) if e.get("origin") in {"explicit", "edited", "manual"}]
    possible = used_chars(protected) <= settings.memory_doc_max_chars and len(protected) < len(active(entries))
    if not possible and user is not None:
        memory = dict((user.settings or {}).get("memory") or {})
        memory["nothing_condensable"] = signature
        user.settings = {**(user.settings or {}), "memory": memory}
    return possible
