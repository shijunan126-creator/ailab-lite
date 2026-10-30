"""Compatibility for checkpoints that pinned the former internal read skills."""

from ..skill_registry import ALIASES

VERSION = "zcode-3c5b227-ailab2"
TOOLS = ["resolve_attachment", "search_library", "read_library_file", "copy_library_file", "view_attachment", "extract_text"]


def metadata():
    return []


def load(name, version=None, *, mode="work"):
    if name not in ALIASES or name == "xlsx" or version not in {None, VERSION, "zcode-3c5b227-ailab1"}:
        raise ValueError("internal_skill_version_unavailable")
    from ..agent.prompts import load_skill

    return load_skill(ALIASES[name], mode=mode)
