"""Static capability/dependency contract; no runtime probing or installation."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from build import BASE, PACKAGES, ROOT, adapted, chunks, dependencies, destination, dump, modes, originals, sha

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


def inventory() -> dict:
    files = originals()
    adapted_files, _ = adapted()
    docker = (ROOT / "images/sbx-office/Dockerfile").read_text()
    python_installed = set(re.findall(r"([\w-]+)==[\d.]+", docker))
    # Declared runtime dependencies are isolated under /opt/ailab-office/node_modules.
    runtime_npm = json.loads((ROOT / "vendor/zcode-office-runtime/package.json").read_text())["dependencies"]
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
            "python": sorted(DISTRIBUTIONS.get(n, n) for n in deps["python"] if DISTRIBUTIONS.get(n, n) not in python_installed),
            "npm": sorted(n for n in deps["npm"] if not name.startswith("office-runtime/") or n not in runtime_npm),
            "binary": sorted(n for n in deps["binary"] if n not in bins),
        }
        for kind, values in missing.items():
            all_missing[kind].update(values)
        dest = destination(name)
        target_modes = modes(name, text) if dest else ["chat", "work"]
        location = f"/opt/skills/platform.{dest[0]}/{dest[1]}" if dest else f"vendor/office-suite/upstream/{name}"
        if "LICENSE" in path.name.upper() or path.name == "LICENSE":
            status, reason = "implemented", "Unmodified license retained with source provenance; not model-visible."
        elif name.startswith("office-runtime/"):
            status, reason = (
                "planned:P3" if "/ui/" in name else "planned:P2",
                "Source pinned only; reader adapter migration and viewer integration remain separate acceptance work.",
            )
        elif "env_setup" in name or "setup.sh" in name:
            status, reason = "blocked", "Host/network installation is disabled; P2 must supply and license offline image dependencies."
        elif any(missing.values()) or deps["dynamic"]:
            status, reason = (
                "planned:P2",
                "Resources retained; missing or dynamic dependencies require image closure and execution acceptance in P2.",
            )
        else:
            status, reason = (
                "planned:P1",
                "Resource built and hashed; module loading is intentionally deferred to P1; execution acceptance remains P2/P5.",
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
        }
        if dest:
            oversized = [c for c in chunks(adapted_files[name].decode()) if "cap_exception" in c]
            if oversized:
                entry["chunk_cap_exceptions"] = oversized
        if name.endswith("scenes/vba.md"):
            entry.update(
                status="blocked",
                reason="Windows Excel/COM macro execution is unavailable in the Linux image; native VBA preservation remains P2.",
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
            elif status == "planned:P1":
                item.update(status="planned:P2", reason="CLI/API retained but command behavior requires P2 execution acceptance.")
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
                    "runtime_location": "Portal isolated viewer frame (planned)",
                    "dependencies": {"npm": sorted(runtime_npm), "python": [], "binary": [], "dynamic": [], "local": []},
                    "missing_from_image": {"npm": [], "python": [], "binary": []},
                    "acceptance_case_id": "OFFICE-" + sha(key.encode())[:12],
                    "status": "planned:P4",
                    "reason": (
                        "Requires P3 document identity/session/tool contract and P4 Portal host integration; no browser side effects in P0."
                    ),
                }
            )
    entries.append(
        {
            "id": "visual-gate:delivery-protocol",
            "kind": "external-contract",
            "source": "presentations-plugin/agents/visual-judge.md",
            "modes": ["work"],
            "runtime_location": "Agent read-only visual review (planned)",
            "dependencies": {"npm": [], "python": [], "binary": [], "dynamic": [], "local": []},
            "missing_from_image": {"npm": [], "python": [], "binary": []},
            "acceptance_case_id": "OFFICE-VISUAL-GATE",
            "status": "blocked",
            "reason": (
                "Source system delivery protocol is absent. P2 must implement the explicitly local AILab gate; "
                "copying the role is not parity."
            ),
        }
    )
    oversized_sources = [e for e in entries if "chunk_cap_exceptions" in e]
    entries.append(
        {
            "id": "module-chunk-cap",
            "kind": "resource-boundary",
            "source": [e["source"] for e in oversized_sources],
            "modes": ["work"],
            "runtime_location": "Agent module budget admission (planned:P1)",
            "dependencies": {"npm": [], "python": [], "binary": [], "dynamic": [], "local": []},
            "missing_from_image": {"npm": [], "python": [], "binary": []},
            "acceptance_case_id": "OFFICE-CHUNK-CAP",
            "status": "blocked",
            "reason": (
                "Three original fenced blocks exceed 2048 estimated tokens. Preserve fences; "
                "P1 must explicitly admit or reject indivisible blocks within the real model budget."
            ),
        }
    )
    return {
        "schema_version": 1,
        "upstream_commit": json.loads((BASE / "UPSTREAM.json").read_text())["commit"],
        "image_baseline_sha256": sha(docker.encode()),
        "scope": "P0 static resource contract, not runtime acceptance",
        "dependency_scan": (
            "Python AST imports/calls, JS/TS literal imports, Markdown examples, shell probes and package manifests; "
            "dynamic sites carry expressions for P2 review."
        ),
        "dependency_notes": [
            "Python absence is measured against explicit Dockerfile pins; transitive modules still need P2 license/version closure.",
            "Existing reader npm modules are private to /opt/ailab-office; skill scripts cannot assume NODE_PATH resolution.",
            (
                "Windows COM/VBA execution requires external Windows Excel and is blocked on the Linux image; "
                "native VBA preservation is separate."
            ),
            "No new dependency, resident service or image layer is installed by P0.",
        ],
        "missing_from_image": {key: sorted(value) for key, value in all_missing.items()},
        "capabilities": entries,
    }


if __name__ == "__main__":
    dump(BASE / "capabilities.json", inventory())
