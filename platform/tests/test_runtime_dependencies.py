"""Every module-level third-party import must be installed by the runtime dependencies alone.

The images install `pip install .` without extras; a package that only arrives through dev or optional extras
works in tests and fails at service start (packaging did, for fetch_packages).
"""

from __future__ import annotations

import ast
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "ailab_platform"


def _name(requirement: str) -> tuple[str, bool]:
    base = re.split(r"[\s;<>=!~\[(]", requirement, maxsplit=1)[0]
    return base.lower().replace("_", "-"), "extra ==" in requirement


def _runtime_closure() -> set[str]:
    todo, seen = ["ailab-platform"], set()
    while todo:
        dist = todo.pop()
        if dist in seen:
            continue
        seen.add(dist)
        try:
            requires = metadata.requires(dist) or []
        except metadata.PackageNotFoundError:
            continue
        for requirement in requires:
            name, optional = _name(requirement)
            # uvicorn[standard] pulls its extra's packages in production too
            if not optional or (dist == "uvicorn" and "standard" in requirement):
                todo.append(name)
    return seen


def _top_level_imports(path: Path) -> set[str]:
    found = set()
    for node in ast.parse(path.read_text()).body:  # module level only: lazy imports guard optional backends
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_module_level_imports_come_from_runtime_dependencies() -> None:
    providers = metadata.packages_distributions()
    closure = _runtime_closure()
    missing = {}
    for path in ROOT.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        for module in _top_level_imports(path) - set(sys.stdlib_module_names) - {"ailab_platform", "__future__"}:
            dists = {d.lower().replace("_", "-") for d in providers.get(module, [])}
            if not dists & closure:
                missing.setdefault(module, []).append(str(path.relative_to(ROOT)))
    assert not missing, f"imported at module level but not a runtime dependency: {missing}"
