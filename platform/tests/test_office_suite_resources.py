"""P0 source preservation, deterministic resource builds, modes and attestation closure."""

import asyncio
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ailab_platform.agent import skill_verification
from ailab_platform.skill_index import build_index, checked_index

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "vendor/office-suite"
SKILLS = ROOT / "images/sbx-office/skills"
spec = importlib.util.spec_from_file_location("office_resource_build", VENDOR / "build.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def test_pinned_originals_have_complete_hash_closure():
    manifest = json.loads((VENDOR / "UPSTREAM.json").read_text())
    assert manifest["commit"] == "a60ba6a7e5a06742270ca605f5d2cc2766afa7e1"
    originals = office.originals()
    assert len(originals) == 217
    assert set(originals) == set(manifest["files"])
    for package, fmt in office.PACKAGES.items():
        assert b"Apache License" in originals[f"{package}/skills/{fmt}/LICENSE.txt"]
        assert any(name.startswith(f"{package}/.") and name.endswith("plugin.json") for name in originals)
    assert b"MIT License" in originals["office-runtime/LICENSE"]
    assert any(name.startswith("office-runtime/fixtures/") for name in originals)
    assert any(name.startswith("office-runtime/scripts/") for name in originals)


def test_rebuild_reproduces_every_generated_byte(tmp_path):
    office.build(tmp_path)
    adapted, _ = office.adapted()
    for fmt in office.PACKAGES.values():
        package = f"platform.{fmt}"
        assert tree(tmp_path / package) == tree(SKILLS / package)
        index = json.loads((tmp_path / package / "modules.json").read_text())
        expected = {name for name in adapted if (dest := office.destination(name)) and dest[0] == fmt}
        assert {m["logical_path"] for m in index["modules"]} == expected
        for module in index["modules"]:
            assert (tmp_path / package / module["path"]).read_bytes() == adapted[module["logical_path"]]
    checked_index(SKILLS)


def test_generated_resources_are_platform_only_and_installers_are_disabled():
    for fmt in office.PACKAGES.values():
        package = SKILLS / f"platform.{fmt}"
        for path in package.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                assert b"zcode" not in data.lower(), path
                assert b"account:" not in data and b"GLM" not in data, path
                if path.suffix == ".py":
                    compile(data, str(path), "exec")
        for setup in package.rglob("setup*.sh"):
            assert setup.read_text().splitlines()[1:4][-1] == "exit 1"
    pdf = (SKILLS / "platform.pdf/scripts/pdf.py").read_text()
    body = pdf.split("def env_fix(argv: list):", 1)[1].split("# ═", 1)[0]
    assert "DependencyMissing" in body and "subprocess.run" not in body


def test_modules_are_lossless_hashed_bounded_and_mode_safe():
    for fmt in office.PACKAGES.values():
        package = SKILLS / f"platform.{fmt}"
        index = json.loads((package / "modules.json").read_text())
        assert "chunk_token_target" not in index and "chunk_token_hard_limit" not in index
        for module in index["modules"]:
            data = (package / module["path"]).read_bytes()
            assert office.sha(data) == module["sha256"]
            assert office.tokens(data.decode()) == module["estimated_tokens"]
            assert "chunks" not in module
            assert set(module["deps"]) <= set(office.originals())
            if module["path"].endswith((".py", ".sh", ".ps1", ".js", ".tex")):
                assert module["modes"] == ["work"]
            if module["path"].startswith("viewer/"):
                assert module["modes"] == ["chat", "work"]


def test_generated_skills_and_shipped_asset_directories_carry_notices(tmp_path):
    office.build(tmp_path)
    originals = office.originals()
    for package, fmt in office.PACKAGES.items():
        skill = tmp_path / f"platform.{fmt}"
        assert (skill / "LICENSE").read_bytes() == originals[f"{package}/skills/{fmt}/LICENSE.txt"]
        assert b"Apache License" in (skill / "LICENSE").read_bytes()
        assert (skill / "NOTICE").read_bytes() == (VENDOR / "NOTICE").read_bytes()
        # Every shipped third-party asset directory must keep its source notices beside the assets.
        source = VENDOR / "compatibility" / skill.name
        for assets in skill.glob("assets/**/*"):
            if assets.is_dir() and any(p.is_file() for p in assets.iterdir()):
                notices = [p for p in (source / assets.relative_to(skill)).iterdir() if re.match(r"LICENSE|NOTICE", p.name, re.I)]
                assert notices, assets
                for notice in notices:
                    assert (assets / notice.name).read_bytes() == notice.read_bytes()
    icons = tmp_path / "platform.pptx/assets/icons"
    assert len(list(icons.glob("*.png"))) == 138
    assert b"ISC License" in (icons / "LICENSE-lucide.txt").read_bytes()


def test_native_template_and_cover_workflows_are_not_summarized():
    original = office.originals()["presentations-plugin/skills/pptx/SKILL.md"].decode()
    adapted = (SKILLS / "platform.pptx/resources/SKILL.md").read_text()
    start = original.index("  • Write a short plan first:")
    end = original.index("## Editing an existing PowerPoint", start)
    assert original[start:end] in adapted
    pdf = (SKILLS / "platform.pdf/resources/SKILL.md").read_text()
    assert "\n```\n\n### Content Centering" not in pdf
    assert "templates 01–05 report, 06–08 academic dark, 09 institutional" in pdf
    assert "**Fallback**: If Report brief content has emoji → reroute to Creative." in pdf
    setup = (SKILLS / "platform.pdf/env_setup/setup.md").read_text()
    assert "| **Core** Python pkg | pikepdf | PDF manipulation (merge/split/encrypt) |" in setup


@pytest.mark.parametrize("resource", ["modules.json", "briefs/report.md", "scripts/pdf.py", "LICENSE", "NOTICE"])
def test_nested_resource_and_index_metadata_are_in_attestation_closure(tmp_path, monkeypatch, resource):
    shutil.copytree(SKILLS, tmp_path / "skills")
    root = tmp_path / "skills"
    expected = checked_index(root)
    target = root / "platform.pdf" / resource
    target.write_bytes(target.read_bytes() + b"\n")
    assert build_index(root)["skills"]["platform.pdf"]["sha256"] != expected["skills"]["platform.pdf"]["sha256"]
    monkeypatch.setattr(skill_verification, "settings", SimpleNamespace(skills_path=root))

    class Broker:
        def _req(self, *args, **kwargs):
            return None

        async def _send(self, req):
            return SimpleNamespace(
                json=lambda: {"verified": True, "lease_id": "lease", "provider_ref": "provider", "image_ref": "image", "index": expected}
            )

    with pytest.raises(skill_verification.SkillRefused):
        asyncio.run(skill_verification.verify_sandbox_skills(Broker(), "lease"))


def test_capability_contract_covers_sources_commands_viewer_and_dependencies(monkeypatch):
    monkeypatch.setitem(sys.modules, "build", office)
    spec = importlib.util.spec_from_file_location("office_inventory", VENDOR / "inventory.py")
    inventory = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inventory)
    actual = inventory.inventory()
    assert actual == json.loads((VENDOR / "capabilities.json").read_text())
    entries = actual["capabilities"]
    boundary = next(e for e in entries if e["id"] == "whole-file-reads")
    assert boundary["status"] == "implemented"
    assert "OFFICE-CHUNK-CAP" not in json.dumps(actual)
    assert {e["source"] for e in entries if e["kind"] in {"source-file", "route", "scene"}} == set(office.originals())
    assert len({e["id"] for e in entries}) == len(entries)
    for item in entries:
        assert item["reason"] and item["acceptance_case_id"] and item["runtime_location"]
        assert item["status"] in {"implemented", "blocked", *{f"planned:P{i}" for i in range(1, 6)}}
    commands = {e["command"] for e in entries if "command" in e}
    assert {"env.fix", "convert.latex", "form.fill", "pivot", "validate"} <= commands
    assert sum(e["kind"] == "viewer-feature" for e in entries) == 36
    assert {"reportlab", "pikepdf", "PyMuPDF"} <= set(actual["missing_from_image"]["python"])
    assert {"playwright", "pdf-lib", "pptxgenjs", "docx"} <= set(actual["missing_from_image"]["npm"])
    assert "tectonic" in actual["missing_from_image"]["binary"]
    assert "COPY images/sbx-office/skills/ /opt/skills/" in (ROOT / "images/sbx-office/Dockerfile").read_text()
