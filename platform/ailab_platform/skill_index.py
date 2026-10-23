"""Deterministic skill manifests; also copied standalone into sandbox images."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def build_index(root: Path) -> dict:
    entries = {}
    for directory in sorted(root.iterdir()):
        if directory.is_symlink():
            raise ValueError("symlink in skills root")
        if not directory.is_dir():
            continue
        manifest = tomllib.loads((directory / "skill.toml").read_text())
        if set(manifest) != {"name", "version", "description", "license", "requires", "roles"}:
            raise ValueError(f"invalid manifest fields: {directory.name}")
        if manifest["name"] != directory.name or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", directory.name):
            raise ValueError("manifest name differs from directory")
        for key in ("version", "description", "license"):
            if not isinstance(manifest[key], str) or not manifest[key].strip():
                raise ValueError(f"invalid {key}")
        if not re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+", manifest["version"]):
            raise ValueError("version must be major.minor.patch")
        for key in ("requires", "roles"):
            if not isinstance(manifest[key], list) or any(not isinstance(v, str) or not v for v in manifest[key]):
                raise ValueError(f"invalid {key}")
        if set(manifest["roles"]) - {"user", "platform_admin", "dept_admin", "auditor"}:
            raise ValueError("unknown role")
        if not (directory / "SKILL.md").is_file():
            raise ValueError("missing SKILL.md")
        digest = hashlib.sha256()
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError("symlinks are not permitted in skills")
            if path.is_file():
                relative = path.relative_to(directory).as_posix().encode()
                digest.update(len(relative).to_bytes(4, "big"))
                digest.update(relative)
                digest.update(path.stat().st_size.to_bytes(8, "big"))
                with path.open("rb") as stream:
                    while chunk := stream.read(65536):
                        digest.update(chunk)
        entries[directory.name] = {**manifest, "sha256": digest.hexdigest()}
    if not entries:
        raise ValueError("empty skills index")
    return {"schema_version": 1, "skills": entries}


class SkillIndexMismatch(ValueError):
    """The skill files on disk are not the ones recorded in index.json."""


def checked_index(root: Path) -> dict:
    actual = build_index(root)
    if json.loads((root / "index.json").read_text()) != actual:
        raise SkillIndexMismatch("skill_index_mismatch: regenerate index.json with skill_index.py in the same change")
    return actual


def main() -> int:
    """Exit 0 when verified, 3 on index drift, 2 on an invalid manifest or missing requirement.

    The Broker runs this inside a leased sandbox and maps the codes to distinct refusals, so drift is never reported
    as a generic failure.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--requirements", action="store_true")
    args = parser.parse_args()
    try:
        index = checked_index(args.root) if args.check else build_index(args.root)
        if args.requirements:
            for skill in index["skills"].values():
                for requirement in skill["requires"]:
                    try:
                        version(requirement)
                    except PackageNotFoundError:
                        if not shutil.which(requirement):
                            raise ValueError(f"missing requirement: {requirement}") from None
    except SkillIndexMismatch as exc:
        print(exc, file=sys.stderr)
        return 3
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not args.check:
        (args.root / "index.json").write_text(json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps(index, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
