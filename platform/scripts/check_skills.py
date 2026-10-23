"""CI and image preflight; run from platform/."""

from pathlib import Path

from ailab_platform.skill_index import checked_index

root = Path(__file__).resolve().parents[2]
checked_index(root / "images/sbx-office/skills")
assert (root / "images/sbx-office/skill_index.py").read_bytes() == (root / "platform/ailab_platform/skill_index.py").read_bytes()
print("Skill manifests, directory hashes and image verifier agree")
