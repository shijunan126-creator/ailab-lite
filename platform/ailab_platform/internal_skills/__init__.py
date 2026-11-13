"""Compatibility for checkpoints that pinned the former internal read skills."""

from ..skill_registry import ALIASES

VERSION = "office-a60ba6a-ailab3"


def metadata():
    return []


def load(name, version=None, *, mode="work"):
    if name not in ALIASES or name == "xlsx" or version not in {None, VERSION, "zcode-3c5b227-ailab1"}:
        raise ValueError("internal_skill_version_unavailable")
    from ..agent.prompts import load_skill

    return load_skill(ALIASES[name], mode=mode)
