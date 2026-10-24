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


class ScopeBoundaryTests(unittest.TestCase):
    def test_all_explicit_documentation_files_are_exempt(self):
        for path in [*module.DOC_FILES, "docs/design/architecture.md"]:
            with self.subTest(path=path):
                self.assertEqual(module.scope([path]), {"code": False, "backend": False, "e2e": False})

    def test_unrecognized_paths_never_inherit_docs_exemption(self):
        for path in (
            "docs/check.py",
            "docs/workflow.yml",
            "docs/tool.sh",
            "docs/package.json",
            "docs/evidence.json",
            "docs/readme.MD",
            "docs-other/readme.md",
            "README.md.py",
            "other/README.md",
            "docs/../runtime.md",
            "docs//design.md",
            "./docs/design.md",
            "/docs/design.md",
            "",
            "platform/portal/../../runtime.py",
            "platform/portal//file.ts",
        ):
            with self.subTest(path=path):
                self.assertEqual(module.scope([path]), {"code": True, "backend": True, "e2e": True})

    def test_dependency_workflow_deploy_and_test_boundaries(self):
        for path in (
            "platform/pyproject.toml",
            "platform/constraints.txt",
            "platform/scripts/lock_deps.sh",
            ".github/workflows/audit.yml",
            ".github/dependabot.yml",
            ".github/tests/test_ci_gate.py",
            ".github/scripts/ci_gate.py",
            "platform/deploy/base/apps/services.yaml",
            "deploy/overlays/lite/kustomization.yaml",
            "platform/tests/test_migrations.py",
            "platform/e2e/conftest.py",
            "tests/test_deploy.sh",
            "images/sbx-office/Dockerfile",
            "platform/portal-new/file.ts",
            ".gitattributes",
            "new folder/文件\n.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(module.scope([path]), {"code": True, "backend": True, "e2e": True})
        for path in (
            "platform/portal/package.json",
            "platform/portal/package-lock.json",
            "platform/portal/next.config.ts",
            "platform/portal/app/page.tsx",
            "platform/portal/tests/api.test.ts",
            "platform/portal/public/icon.svg",
        ):
            with self.subTest(path=path):
                self.assertEqual(module.scope([path]), {"code": True, "backend": False, "e2e": True})


class ScopeGitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "CI")
        self.git("config", "user.email", "ci@example.invalid")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True).strip()

    def write(self, path, content="fixture"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def run_scope(self, event_name="pull_request", base=None, head=None, event_text=None):
        event = Path(self.directory.name) / "event.json"
        output = Path(self.directory.name) / "output"
        output.unlink(missing_ok=True)
        event.write_text(
            event_text if event_text is not None else json.dumps({"pull_request": {"base": {"sha": base}, "head": {"sha": head}}})
        )
        env = {**os.environ, "GITHUB_EVENT_NAME": event_name, "GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output)}
        result = subprocess.run([sys.executable, module.__file__], cwd=self.root, env=env, capture_output=True, text=True, check=False)
        return result, output.read_text() if output.exists() else ""

    def assert_scope(self, base, head, backend=True, code=True):
        result, output = self.run_scope(base=base, head=head)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output, f"code={str(code).lower()}\nbackend={str(backend).lower()}\ne2e={str(code).lower()}\n")
        self.assertEqual(result.stdout, output)

    def test_renames_into_and_out_of_documentation_keep_both_sides(self):
        self.write("runtime.py")
        base = self.commit()
        (self.root / "docs").mkdir()
        self.git("mv", "runtime.py", "docs/runtime.md")
        head = self.commit()
        self.assert_scope(base, head)
        self.git("mv", "docs/runtime.md", "runtime.py")
        self.assert_scope(head, self.commit())

    def test_portal_rename_to_docs_still_runs_portal_and_browser(self):
        self.write("platform/portal/lib/api.ts")
        base = self.commit()
        (self.root / "docs").mkdir()
        self.git("mv", "platform/portal/lib/api.ts", "docs/api.md")
        self.assert_scope(base, self.commit(), backend=False)

    def test_deletions_and_unusual_filenames_remain_visible(self):
        for path in ("platform/constraints.txt", "platform/portal/package-lock.json", "new folder/文件\n.py"):
            with self.subTest(path=path):
                self.write(path)
                base = self.commit()
                (self.root / path).unlink()
                self.assert_scope(base, self.commit(), backend=not path.startswith("platform/portal/"))

    def test_diff_uses_merge_base_not_the_new_base_tip(self):
        self.write("README.md")
        ancestor = self.commit()
        self.git("checkout", "-qb", "topic")
        self.write("docs/topic.md")
        head = self.commit()
        self.git("checkout", "-q", "--detach", ancestor)
        self.write("runtime.py")
        base = self.commit()
        self.assert_scope(base, head, backend=False, code=False)

    def test_empty_pr_diff_and_docs_deletion_select_no_services(self):
        self.write("README.md")
        base = self.commit()
        self.assert_scope(base, base, backend=False, code=False)
        self.write("docs/design.md")
        base = self.commit()
        (self.root / "docs/design.md").unlink()
        self.assert_scope(base, self.commit(), backend=False, code=False)

    def test_push_and_manual_runs_always_select_all_without_reading_event(self):
        for name in ("push", "workflow_dispatch", "schedule"):
            with self.subTest(name=name):
                result, output = self.run_scope(event_name=name, event_text="not JSON")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, "code=true\nbackend=true\ne2e=true\n")

    def test_invalid_event_or_unavailable_git_history_never_emits_skip_outputs(self):
        for event in ("{", "{}", json.dumps({"pull_request": {"base": {"sha": "missing"}, "head": {"sha": "absent"}}})):
            with self.subTest(event=event):
                result, output = self.run_scope(event_text=event)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, "")
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
