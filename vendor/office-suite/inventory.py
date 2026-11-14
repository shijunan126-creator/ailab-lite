"""Implementation inventory with explicit execution evidence; no runtime probing or installation."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from build import BASE, PACKAGES, ROOT, dependencies, destination, dump, modes, originals, sha

# Import names whose distribution or executable spelling differs.
DISTRIBUTIONS = {
    "docx": "python-docx",
    "pptx": "python-pptx",
    "PIL": "Pillow",
    "fitz": "PyMuPDF",
    "cv2": "opencv-python",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "win32com": "pywin32",
}

# Evidence is the test that exercises the capability, not a session note.
P5_EVIDENCE = "platform/tests/contracts/test_office_production.py"
VIEWER_EVIDENCE = "platform/portal/tests/office-view.test.tsx"
REVIEW_EVIDENCE = "platform/tests/test_artifact_review.py"


def inventory() -> dict:
    files = originals()
    docker = (ROOT / "images/sbx-office/Dockerfile").read_text()
    python_lock = (ROOT / "images/sbx-office/requirements.lock").read_text()
    python_installed = {name.lower().replace("_", "-") for name in re.findall(r"([\w-]+)==[\d.]+", python_lock)}
    production_npm = json.loads((ROOT / "images/sbx-office/package.json").read_text())["dependencies"]
    # Declared runtime dependencies are isolated under /opt/ailab-office/node_modules.
    runtime_npm = json.loads((ROOT / "vendor/office-suite/runtime/package.json").read_text())["dependencies"]
    bins = {
        "python",
        "python3",
        "node",
        "bash",
        "sh",
        "zip",
        "unzip",
        "soffice",
        "libreoffice",
        "pdftoppm",
        "pdftotext",
        "pdfinfo",
        "fc-list",
        "fc-match",
        "chromium",
        "pandoc",
        "xelatex",
        "latexmk",
        "tesseract",
        "convert",
    }
    entries, all_missing = [], {"python": set(), "npm": set(), "binary": set()}
    for name, data in sorted(files.items()):
        path = Path(name)
        text = data.decode(errors="replace")
        deps = dependencies(name, data)
        # Local imports are packaged resources rather than missing PyPI distributions.
        local = []
        for module in list(deps["python"]):
            if any(Path(other).stem == module for other in files if Path(other).parts[0] == path.parts[0]) or module in {
                "scripts",
                "templates",
                "cover_render",
                "skills",
            }:
                deps["python"].remove(module)
                local.append(module)
        for module in list(deps["npm"]):
            if module.startswith("#") or module == "@zcode/office-runtime":
                deps["npm"].remove(module)
                local.append(module)
        deps["local"] = sorted(local)
        if name.endswith((".sh", ".ps1")):
            deps["binary"] = sorted(
                set(deps["binary"])
                | {
                    value
                    for pair in re.findall(
                        r"(?:command -v|which|Get-Command)\s+([\w.-]+)|^\s*(curl|wget|npm|npx|pip|pip3|brew|sudo|powershell)\b", text, re.M
                    )
                    for value in pair
                    if value
                }
            )
        if path.name == "package.json":
            package = json.loads(text)
            deps["npm"] = sorted(set(deps["npm"]) | set(package.get("dependencies", {})) | set(package.get("devDependencies", {})))
        if "@zcode/office-runtime" in deps["npm"]:
            deps["npm"].remove("@zcode/office-runtime")
            deps["local"].append("office-runtime")
        missing = {
            "python": sorted(
                DISTRIBUTIONS.get(n, n) for n in deps["python"] if DISTRIBUTIONS.get(n, n).lower().replace("_", "-") not in python_installed
            ),
            "npm": sorted(n for n in deps["npm"] if n not in (runtime_npm if name.startswith("office-runtime/") else production_npm)),
            "binary": sorted(n for n in deps["binary"] if n not in bins),
        }
        for kind, values in missing.items():
            all_missing[kind].update(values)
        dest = destination(name)
        target_modes = modes(name, text) if dest else ["chat", "work"]
        location = f"/opt/skills/platform.{dest[0]}/{dest[1]}" if dest else f"vendor/office-suite/upstream/{name}"
        if name.startswith("office-runtime/"):
            relative = path.relative_to("office-runtime").as_posix()
            if relative.startswith("ui/"):
                location = "platform/portal/office/build.mjs -> isolated per-format viewer bundle"
            elif relative.startswith("src/"):
                location = f"/opt/ailab-office/{relative}"
            else:
                location = f"build/test only: vendor/office-suite/upstream/{name}"
        if "LICENSE" in path.name.upper() or path.name == "LICENSE":
            status, reason = "implemented", "Unmodified license retained with source provenance; not model-visible."
        elif name.startswith("office-runtime/"):
            status, reason = (
                "implemented",
                "Runtime integrated through Portal/reader adapters; local samples do not prove full Office fidelity.",
            )
        elif "env_setup" in name or "setup.sh" in name:
            status, reason = "blocked", "Installation disabled; use preinstalled dependencies, never run these installers."
        else:
            status, reason = (
                "implemented",
                "Packaged, hashed and mode-gated through load_skill; individual recipes require execution acceptance.",
            )
        entry = {
            "id": "source:" + name,
            "source": name,
            "kind": "route" if "/routes/" in name else "scene" if "/scenes/" in name else "source-file",
            "modes": target_modes,
            "runtime_location": location,
            "dependencies": deps,
            "missing_from_image": missing,
            "acceptance_case_id": "OFFICE-" + sha(name.encode())[:12],
            "status": status,
            "reason": reason,
            "evidence": [P5_EVIDENCE],
            "execution_validation": "not individually accepted; static missing/dynamic dependencies require task-specific verification",
        }
        if name.endswith("scenes/vba.md"):
            entry.update(
                status="blocked",
                reason="Windows Excel/COM macro execution is unavailable in the Linux image; preserving VBA is separate from execution.",
            )
        entries.append(entry)
        commands = set(json.loads(text).get("scripts", {})) if path.name == "package.json" else set()
        commands.update(re.findall(r'@(?:cmd|command)\(["\']([^"\']+)["\']\)', text))
        commands.update(re.findall(r'\.add_parser\(["\']([^"\']+)["\']', text))
        if name.endswith("scripts/document.py"):
            tree = ast.parse(text)
            commands.update(
                f"{node.name}.{method.name}"
                for node in tree.body
                if isinstance(node, ast.ClassDef)
                for method in node.body
                if isinstance(method, ast.FunctionDef) and not method.name.startswith("_")
            )
        for command in sorted(commands):
            item = {
                **entry,
                "id": f"command:{name}#{command}",
                "kind": "python-api" if name.endswith("scripts/document.py") else "cli-command",
                "command": command,
                "modes": ["work"],
                "acceptance_case_id": "OFFICE-" + sha((name + "#" + command).encode())[:12],
            }
            if command == "env.fix":
                item.update(
                    status="blocked",
                    reason="Runtime dependency installation deliberately disabled; report missing preinstalled dependencies instead.",
                )
            elif status != "blocked":
                item.update(
                    status="planned:P5",
                    reason="Command shipped; representative flows passed, but this command lacks individual execution acceptance.",
                )
            entries.append(item)
    for fmt in PACKAGES.values():
        for feature in (
            "open",
            "outline",
            "zoom",
            "search",
            "selection",
            "reveal-highlight",
            "current-view",
            "capture",
            "selection-reference",
        ):
            key = f"viewer:{fmt}:{feature}"
            entries.append(
                {
                    "id": key,
                    "kind": "viewer-feature",
                    "source": f"office-runtime/ui/viewers/{fmt.title()}View.tsx",
                    "modes": ["chat", "work"],
                    "runtime_location": "platform/portal/components/preview/office-view.tsx -> isolated per-format viewer frame",
                    "dependencies": {"npm": sorted(runtime_npm), "python": [], "binary": [], "dynamic": [], "local": []},
                    "missing_from_image": {"npm": [], "python": [], "binary": []},
                    "acceptance_case_id": "OFFICE-" + sha(key.encode())[:12],
                    "status": "implemented",
                    "reason": "Native viewer and document_view connected; verification covers recorded local samples.",
                    "evidence": [VIEWER_EVIDENCE, "platform/e2e/test_office_native.py"],
                    "execution_validation": "limited local samples; no claim of all browsers, long-duration or full format fidelity",
                }
            )
    entries.append(
        {
            "id": "visual-gate:delivery-protocol",
            "kind": "external-contract",
            "source": "presentations-plugin/agents/visual-judge.md",
            "modes": ["work"],
            "runtime_location": "Upstream system delivery protocol (not supplied)",
            "dependencies": {"npm": [], "python": [], "binary": [], "dynamic": [], "local": []},
            "missing_from_image": {"npm": [], "python": [], "binary": []},
            "acceptance_case_id": "OFFICE-VISUAL-GATE",
            "status": "blocked",
            "reason": (
                "Source system delivery protocol is absent. The implemented local AILab review_artifact contract is separate; "
                "copying the role is not upstream parity."
            ),
        }
    )
    entries.append(
        {
            "id": "visual-gate:ailab-review",
            "kind": "local-contract",
            "source": "platform/ailab_platform/agent/tools.py",
            "modes": ["work"],
            "runtime_location": "review_artifact -> Broker -> active Work sandbox",
            "dependencies": {"npm": [], "python": [], "binary": [], "dynamic": [], "local": []},
            "missing_from_image": {"npm": [], "python": [], "binary": []},
            "acceptance_case_id": "OFFICE-AILAB-VISUAL-REVIEW",
            "status": "implemented",
            "reason": "Bounded structural/page-image evidence; pending_model_judgment is not an automatic quality pass.",
            "evidence": [REVIEW_EVIDENCE],
            "execution_validation": "representative DOCX/XLSX/PPTX/PDF tasks, not every recipe or independent visual model",
        }
    )
    entries.append(
        {
            "id": "whole-file-reads",
            "kind": "resource-boundary",
            "source": [],
            "modes": ["chat", "work"],
            "runtime_location": "Agent ModelPolicy admission",
            "dependencies": {"npm": [], "python": [], "binary": [], "dynamic": [], "local": []},
            "missing_from_image": {"npm": [], "python": [], "binary": []},
            "acceptance_case_id": "OFFICE-WHOLE-FILE",
            "status": "implemented",
            "reason": "Whole-file reads only; reject insufficient remaining context explicitly, never truncate.",
        }
    )
    return {
        "schema_version": 1,
        "upstream_commit": json.loads((BASE / "UPSTREAM.json").read_text())["commit"],
        "image_baseline_sha256": sha(docker.encode()),
        "scope": "Implementation inventory; resource availability, command acceptance and external prerequisites are distinct.",
        "dependency_scan": (
            "Python AST imports/calls, JS/TS literal imports, Markdown examples, shell probes and package manifests; "
            "dynamic sites carry expressions for P2 review."
        ),
        "dependency_notes": [
            "Python absence uses the hash-locked image closure; dynamic imports still need execution evidence.",
            "Reader modules stay in /opt/ailab-office; production scripts use /opt/office-production via NODE_PATH.",
            (
                "Windows COM/VBA execution requires external Windows Excel and is blocked on the Linux image; "
                "native VBA preservation is separate."
            ),
            "P2 image installs the offline production closure at build time; no new resident service or runtime package installation.",
        ],
        "missing_from_image": {key: sorted(value) for key, value in all_missing.items()},
        "capabilities": entries,
    }


if __name__ == "__main__":
    dump(BASE / "capabilities.json", inventory())
