"""Identity and verified, read-only platform skill catalog."""

from functools import lru_cache
from pathlib import Path

from .config import settings
from .skill_index import checked_index
from .skillfiles import frontmatter

BASE_SKILLS = ("platform.pdf", "platform.docx", "platform.xlsx", "platform.pptx")
ALIASES = {
    "xlsx": "platform.xlsx",
    "platform.document.read": "platform.docx",
    "platform.pdf.read": "platform.pdf",
    "platform.spreadsheet.read": "platform.xlsx",
    "platform.presentation.read": "platform.pptx",
}


def resolve_name(name: str) -> str:
    return ALIASES.get(name, name)


def kind(name: str) -> str:
    return "base" if name in BASE_SKILLS else "builtin"


@lru_cache
def catalog() -> dict[str, dict]:
    """name -> manifest fields plus title/description/path for every <skills_dir>/<name>/SKILL.md.

    The directory must match its index.json; this runs once per process, so drift needs a restart and a new index.
    """
    root: Path = settings.skills_path
    if not root.is_dir():
        raise FileNotFoundError(f"skills directory not found at {root}; set AILAB_SKILLS_DIR")
    if not any(root.glob("*/SKILL.md")):
        raise FileNotFoundError(f"no skills (*/SKILL.md) under {root}; set AILAB_SKILLS_DIR")
    manifest = checked_index(root)["skills"]
    found = {}
    for skill_md in sorted(root.glob("*/SKILL.md")):
        meta = frontmatter(skill_md.read_text(encoding="utf-8"))
        name = meta.get("name") or skill_md.parent.name
        if name != skill_md.parent.name:
            raise ValueError(f"SKILL.md name {name!r} differs from directory {skill_md.parent.name!r}")
        found[name] = {**manifest[name], "title": meta.get("title") or name, "description": meta.get("description", ""), "path": skill_md}
    if not found:
        raise FileNotFoundError(f"no skills (*/SKILL.md) under {root}; set AILAB_SKILLS_DIR")
    return found
