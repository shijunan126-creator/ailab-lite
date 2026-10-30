"""Validation and content-addressed draft file manifests."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from .. import skillfiles

MAX_TEXT = 512 * 1024
SAFE_IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def checked_path(path: str) -> str:
    if not path or len(path) > 1024 or "\\" in path or "\x00" in path or path.startswith("/"):
        raise skillfiles.SkillFileError("文件路径不安全")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts) or PurePosixPath(path).is_absolute():
        raise skillfiles.SkillFileError("文件路径不安全")
    return path


def classify(path: str, content: bytes) -> str:
    if PurePosixPath(path).suffix.lower() in SAFE_IMAGES:
        return "image"
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "binary" if b"\x00" in content else "text"


def validate_manifest(files: list[dict], *, require_skill: bool = False) -> None:
    active = [item for item in files if item["change"] != "deleted"]
    paths = [checked_path(item["path"]) for item in active]
    if len(paths) != len(set(paths)) or any(path.startswith(other + "/") for path in paths for other in paths):
        raise skillfiles.SkillFileError("文件路径重复或与目录冲突")
    if len(active) > skillfiles.MAX_ENTRIES or sum(item["size"] for item in active) > skillfiles.MAX_UNPACKED:
        raise skillfiles.SkillFileError("草稿文件数量或总大小超过限制")
    skill = next((item for item in active if item["path"] == "SKILL.md"), None)
    if require_skill and not skill:
        raise skillfiles.SkillFileError("草稿缺少 SKILL.md")
    if skill and skill["size"] > skillfiles.MAX_SKILL_MD:
        raise skillfiles.SkillFileError("SKILL.md 超过 100 KB")


def store_file(storage, user_id: str, draft_id: str, path: str, content: bytes, change: str) -> dict:
    checked_path(path)
    if len(content) > skillfiles.MAX_UNPACKED:
        raise skillfiles.SkillFileError("文件超过大小限制")
    digest = hashlib.sha256(content).hexdigest()
    key = f"skill-drafts/{user_id}/{draft_id}/{digest}"
    storage.put_bytes(key, content)
    return {"path": path, "size": len(content), "sha256": digest, "kind": classify(path, content), "change": change, "blob": key}


def update_metadata(draft, storage):
    md = next((f for f in draft.files or [] if f["path"] == "SKILL.md" and f["change"] != "deleted"), None)
    meta = skillfiles.frontmatter(storage.get_bytes(md["blob"]).decode("utf-8", "replace")) if md else {}
    draft.name, draft.title, draft.description = (meta.get(k) for k in ("name", "title", "description"))
