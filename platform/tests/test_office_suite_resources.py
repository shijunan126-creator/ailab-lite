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
            assert len(setup.read_text().splitlines()) == 4, setup
        for setup in package.rglob("setup*.ps1"):
            assert setup.read_text().splitlines() == ['throw "Dependencies are preinstalled; report the missing one and stop."']


# Text the sandbox cannot honour, or that would put the upstream vendor into a user's file.
FOREIGN = [
    rb"(?i)\bz\.ai\b",
    rb"(?i)chatglm|zhipu",
    rb"/mnt/skills",
    rb"fonts\.googleapis",
    rb"(?i)install on demand|MUST install|sudo |tuna\.tsinghua|npmmirror",
    rb"(TTFont\(|addfont\(|fname=).*truetype/(chinese|english)/",
    rb"Tectonic (auto-downloads|pulls)",
    rb"Remote URLs \(`http://` / `https://`\) are fine",
]


def test_generated_resources_only_promise_what_the_image_has():
    hits = []
    for fmt in office.PACKAGES.values():
        for path in (SKILLS / f"platform.{fmt}").rglob("*"):
            if not path.is_file() or path.name in {"LICENSE", "NOTICE", "modules.json"} or path.suffix == ".png":
                continue
            data = path.read_bytes()
            for pattern in FOREIGN:
                for line in data.splitlines():
                    # The resource frontmatter names the upstream author; it never reaches a user file.
                    if re.search(pattern, line) and line.strip() != b"author: Z.AI":
                        hits.append(f"{path.relative_to(SKILLS)}: {line[:120]!r}")
    assert not hits, "\n".join(hits)
    pdf = (SKILLS / "platform.pdf/scripts/pdf.py").read_text()
    import ast

    body = ast.unparse(next(node for node in ast.parse(pdf).body if isinstance(node, ast.FunctionDef) and node.name == "env_fix"))
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
        if item["kind"] == "viewer-feature":
            assert item["status"] == "implemented"
            assert all((ROOT / evidence).is_file() for evidence in item["evidence"])
            assert "planned" not in item["runtime_location"]
        if item["kind"] in {"cli-command", "python-api"}:
            assert item["status"] in {"planned:P5", "blocked"}
    commands = {e["command"] for e in entries if "command" in e}
    assert {"env.fix", "convert.latex", "form.fill", "pivot", "validate"} <= commands
    assert sum(e["kind"] == "viewer-feature" for e in entries) == 36
    assert next(e for e in entries if e["id"] == "visual-gate:delivery-protocol")["status"] == "blocked"
    assert next(e for e in entries if e["id"] == "visual-gate:ailab-review")["status"] == "implemented"
    assert not {"reportlab", "pikepdf", "PyMuPDF"} & set(actual["missing_from_image"]["python"])
    assert not {"playwright", "pdf-lib", "pptxgenjs", "docx"} & set(actual["missing_from_image"]["npm"])
    assert "tectonic" in actual["missing_from_image"]["binary"]
    assert "COPY images/sbx-office/skills/ /opt/skills/" in (ROOT / "images/sbx-office/Dockerfile").read_text()


def test_runtime_rebase_is_reproducible_and_keeps_original_sources(tmp_path):
    before = office.originals()
    office.build_runtime(tmp_path / "runtime")
    expected = tree(VENDOR / "runtime")
    expected = {
        name: data for name, data in expected.items() if not name.startswith("node_modules/") and name not in {"runner.mjs", "bench.mjs"}
    }
    assert tree(tmp_path / "runtime") == expected
    assert office.originals() == before


@pytest.mark.parametrize(
    ("path", "data", "error"),
    [
        ("SKILL.md", b"a" * 100_000, None),
        ("resources/SKILL.md", b"a" * 100_001, "100000 bytes"),
        ("reference.md", b"a" * (256 * 1024), None),
        ("reference.md", b"a" * (256 * 1024 + 1), "262144 bytes"),
        ("reference.md", b"a\n" * 2000, None),
        ("reference.md", b"a\n" * 2001, "2000 lines"),
        ("SKILL.md", b"a\n" * 2001, None),
        ("reference.md", "中".encode() * 87382, "262144 bytes"),
        ("asset.png", b"\xff" * 300_000, None),
    ],
)
def test_readable_resource_loader_boundaries(path, data, error):
    if error:
        with pytest.raises(ValueError, match=error):
            office.validate_readable_resource(path, data)
    else:
        office.validate_readable_resource(path, data)


def test_build_rejects_oversized_resource_before_manifest(tmp_path, monkeypatch):
    files, trace = office.adapted()
    files["documents-plugin/skills/docx/references/oversized.md"] = b"line\n" * 2001
    monkeypatch.setattr(office, "adapted", lambda: (files, trace))
    with pytest.raises(ValueError, match="2000 lines"):
        office.build(tmp_path)
    assert not (tmp_path / "platform.docx/modules.json").exists()


def test_line_limit_adaptation_preserves_executable_python_ast():
    import ast

    patches = json.loads((VENDOR / "patches/007-readable-resource-limits.json").read_text())
    for change in patches:
        edit = change["edits"][0]
        assert ast.dump(ast.parse(edit["before"])) == ast.dump(ast.parse(edit["after"]))
        assert len(edit["after"].splitlines()) <= 2000
