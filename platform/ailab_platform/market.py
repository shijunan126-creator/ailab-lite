"""Official skill marketplace: a small, curated catalog shipped with the platform.

Phase 1 is intentionally read-only: the catalog is code, not user content. Installing copies the package into the
user's personal skill library, where the normal Worker scan, review policy, versioning and deletion rules apply.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

MARKET_ROOT = Path(__file__).with_name("market_skills")

CATALOG: tuple[dict, ...] = ()


def listing() -> list[dict]:
    return [dict(item) for item in CATALOG]


def find(slug: str) -> dict | None:
    return next((dict(item) for item in CATALOG if item["slug"] == slug), None)


def package_zip(slug: str) -> bytes:
    """Build the installable ZIP from the checked-in package directory."""
    entry = find(slug)
    if entry is None:
        raise KeyError(slug)
    root = MARKET_ROOT / slug
    if not root.is_dir():
        raise FileNotFoundError(f"market package missing: {slug}")
    if not (root / "SKILL.md").is_file():
        raise FileNotFoundError(f"market package has no SKILL.md: {slug}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                archive.write(path, path.relative_to(root).as_posix())
    return buffer.getvalue()
