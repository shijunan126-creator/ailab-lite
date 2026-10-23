import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("ci_scope", Path(__file__).parents[1] / "scripts/ci_scope.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ScopeTests(unittest.TestCase):
    def test_documentation_only_does_not_start_services(self):
        self.assertEqual(
            module.scope(["docs/progress/current.md", "AGENTS.md"]),
            {"code": False, "backend": False, "e2e": False},
        )

    def test_portal_still_gets_a_production_build_and_browser(self):
        self.assertEqual(
            module.scope(["platform/portal/lib/api.ts"]),
            {"code": True, "backend": False, "e2e": True},
        )

    def test_unknown_and_shared_changes_fail_closed_to_all_suites(self):
        for path in [
            "platform/ailab_platform/contracts/usage.py",
            "platform/constraints.txt",
            "deploy.sh",
            ".github/workflows/ci.yml",
            ".github/scripts/ci_scope.py",
            "new-runtime/file",
            "images/sbx-office/skills/pdf/SKILL.md",
        ]:
            with self.subTest(path=path):
                self.assertEqual(module.scope([path]), {"code": True, "backend": True, "e2e": True})

    def test_mixed_pr_cannot_hide_backend_change_behind_later_docs(self):
        self.assertTrue(module.scope(["platform/ailab_platform/api/main.py", "docs/progress/current.md"])["backend"])

    def test_real_pr_diff_keeps_earlier_code_when_latest_commit_is_docs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

            def commit():
                git("add", ".")
                git("-c", "user.name=CI", "-c", "user.email=ci@example.invalid", "commit", "-qm", "fixture")
                return git("rev-parse", "HEAD")

            git("init", "-q")
            (root / "README.md").write_text("baseline")
            base = commit()
            (root / "runtime.py").write_text("pass")
            commit()
            (root / "README.md").write_text("later documentation")
            head = commit()
            event, output = root / "event.json", root / "output"
            event.write_text(json.dumps({"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}))
            subprocess.run(
                [sys.executable, str(Path(module.__file__).resolve())],
                cwd=root,
                check=True,
                capture_output=True,
                env={**os.environ, "GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output)},
            )
            self.assertIn("backend=true", output.read_text())


if __name__ == "__main__":
    unittest.main()
