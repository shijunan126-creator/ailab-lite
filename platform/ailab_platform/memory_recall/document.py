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
            lines.append(f"- [{e['id']}] [{date}] {model_text(e['content'])}")
    return "\n".join(lines)


def used_chars(entries):
    return len(render(entries))
