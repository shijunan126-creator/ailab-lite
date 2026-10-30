"""Fence-aware answer views. No settings, persistence or model calls."""

import re
from collections import Counter
from typing import Literal, TypedDict

from ..contracts.genui import component_names


class Part(TypedDict):
    kind: Literal["md", "ui"]
    text: str
    closed: bool
    index: int


_FENCE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z_0-9]*)\s*\(")
_STRINGS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


def _parts(text: str) -> list[tuple[Part, str]]:
    out = []
    start = body = offset = 0
    fence = ""
    ui = False

    def add(kind, value, closed, raw):
        out.append((Part(kind=kind, text=value, closed=closed, index=len(out)), raw))

    # Split on \n only, like the Portal: str.splitlines also breaks on U+2028 and friends.
    for line in re.findall(r"[^\n]*\n|[^\n]+", text):
        match = _FENCE.match(line.rstrip("\r\n"))
        if match:
            ticks, info = match.groups()
            if fence:
                if ticks[0] == fence[0] and len(ticks) >= len(fence) and not info.strip():
                    if ui:
                        add("ui", text[body:offset], True, text[start : offset + len(line)])
                        start = offset + len(line)
                    fence, ui = "", False
            else:
                fence = ticks
                ui = ticks[0] == "`" and info.strip() == "openui"
                if ui:
                    if offset > start:
                        add("md", text[start:offset], True, text[start:offset])
                    start, body = offset, offset + len(line)
        offset += len(line)
    if ui:
        add("ui", text[body:], False, text[start:])
    elif start < len(text):
        add("md", text[start:], True, text[start:])
    return out


def split(text: str) -> list[Part]:
    return [part for part, _ in _parts(text)]


def _components(source: str) -> Counter:
    names = component_names()
    return Counter(name if name in names else "unknown" for name in _CALL.findall(_STRINGS.sub('""', source)) if name != "ToAssistant")


def inspect(text: str, *, max_block_chars: int = 32000) -> dict:
    blocks = [part for part in split(text) if part["kind"] == "ui"]
    components = Counter()
    for block in blocks:
        components.update(_components(block["text"]))
    return {
        "blocks": len(blocks),
        "unclosed": sum(not block["closed"] for block in blocks),
        "oversize": sum(len(block["text"]) > max_block_chars for block in blocks),
        "components": dict(components),
    }


def placeholder(source: str) -> str:
    components = _components(source)
    return "[界面：" + ("、".join(name + (f"×{count}" if count > 1 else "") for name, count in components.items()) or "空") + "]"


def history_sources(sources: object, limit: int = 10) -> list[dict]:
    """The web pages a turn's tools fetched. Tool traffic is not replayed into later turns, so without this a model
    reading its own earlier answer sees cited numbers and no sign it ever looked anything up, and disowns them."""
    if not isinstance(sources, list):
        return []
    picked = [s for s in sources if isinstance(s, dict) and isinstance(s.get("url"), str) and s["url"]]
    return [{"title": str(s.get("title") or "")[:120], "url": s["url"][:300]} for s in picked[:limit]]


def for_history(text: str, keep_ui: bool, limit: int) -> str:
    out, used = [], 0
    for part, raw in _parts(text):
        value = raw if part["kind"] == "md" or keep_ui else placeholder(part["text"])
        if used + len(value) > limit and part["kind"] == "ui":
            value = placeholder(part["text"])
        if used + len(value) > limit:
            if part["kind"] == "md":
                out.append(value[: max(0, limit - used)])
            break
        out.append(value)
        used += len(value)
    return "".join(out)
