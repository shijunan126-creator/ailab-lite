"""The sandbox query programs and the formatting around them.

The programs are executed here with the current interpreter (as ``python3``) and
fake ``rg``/``fd`` executables on PATH, so the parts that must run inside a
sandbox are covered without a container.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ailab_platform.agent import workspace_query
from ailab_platform.agent.workspace_query import (
    FIND_PROGRAM,
    GREP_PROGRAM,
    LS_PROGRAM,
    QueryError,
    find_files,
    list_directory,
    search_text,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the query programs are POSIX shell commands")


class FakeBroker:
    """A Broker that returns one canned exec result."""

    def __init__(self, stdout: str = "", exit_code: int = 0, stderr: str = "") -> None:
        self.stdout, self.exit_code, self.stderr = stdout, exit_code, stderr
        self.calls: list[dict] = []

    async def exec(self, lease_id: str, command: str, timeout: int | None = None) -> dict:
        self.calls.append({"lease_id": lease_id, "command": command, "timeout": timeout})
        return {"exit_code": self.exit_code, "stdout": self.stdout, "stderr": self.stderr, "duration_ms": 1}


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    """A PATH entry with ``python3`` plus whatever the test adds."""
    directory = tmp_path / "bin"
    directory.mkdir()
    (directory / "python3").symlink_to(sys.executable)
    return directory


def run_program(bin_dir: Path, program: str, args: list, cwd: Path | None = None) -> dict:
    command = workspace_query._command(program, args)
    # A minimal PATH: the developer's own rg/fd must not leak into a test that fakes them.
    environment = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"}
    completed = subprocess.run(
        ["/bin/sh", "-c", command], capture_output=True, text=True, timeout=60, env=environment, cwd=str(cwd) if cwd else None
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def fake_tool(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!{sys.executable}\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def fake_rg(bin_dir: Path, events: list[dict]) -> None:
    body = "import json\n" + "".join(f"print(json.dumps({event!r}))\n" for event in events)
    fake_tool(bin_dir, "rg", body)


class TestLsProgram:
    def test_lists_one_level_with_directories_marked(self, tmp_path, bin_dir):
        root = tmp_path / "work"
        (root / "sub").mkdir(parents=True)
        (root / "b.txt").write_text("x")
        (root / ".hidden").write_text("x")
        payload = run_program(bin_dir, LS_PROGRAM, [json.dumps({"path": str(root), "limit": 500})])
        assert payload["ok"] is True
        assert payload["entries"] == [".hidden", "b.txt", "sub/"]
        assert payload["limit_reached"] is False

    def test_reports_the_entry_limit(self, tmp_path, bin_dir):
        root = tmp_path / "work"
        root.mkdir()
        for name in ("a", "b", "c"):
            (root / name).write_text("x")
        payload = run_program(bin_dir, LS_PROGRAM, [json.dumps({"path": str(root), "limit": 2})])
        assert payload["entries"] == ["a", "b"] and payload["limit_reached"] is True

    def test_missing_path_and_file_are_named(self, tmp_path, bin_dir):
        missing = run_program(bin_dir, LS_PROGRAM, [json.dumps({"path": str(tmp_path / "nope"), "limit": 10})])
        assert missing["ok"] is False and "Path not found" in missing["error"]
        afile = tmp_path / "plain.txt"
        afile.write_text("x")
        notdir = run_program(bin_dir, LS_PROGRAM, [json.dumps({"path": str(afile), "limit": 10})])
        assert notdir["ok"] is False and "Not a directory" in notdir["error"]


class TestGrepProgram:
    def test_parses_rg_json_into_matching_lines(self, tmp_path, bin_dir):
        root = tmp_path / "work"
        root.mkdir()
        fake_rg(
            bin_dir,
            [
                {"type": "begin"},
                {"type": "match", "data": {"path": {"text": "a.txt"}, "line_number": 3, "lines": {"text": "hello\n"}}},
                {"type": "match", "data": {"path": {"text": "./b.txt"}, "line_number": 7, "lines": {"text": "world\r\n"}}},
                {"type": "end"},
            ],
        )
        payload = run_program(bin_dir, GREP_PROGRAM, [json.dumps({"path": str(root), "pattern": "x", "limit": 100}), 51200, 500])
        assert payload["ok"] is True
        assert payload["lines"] == ["a.txt:3: hello", "b.txt:7: world"]
        assert payload["limit_reached"] is False

    def test_stops_at_the_match_limit(self, tmp_path, bin_dir):
        root = tmp_path / "work"
        root.mkdir()
        events = [
            {"type": "match", "data": {"path": {"text": "a.txt"}, "line_number": n, "lines": {"text": f"hit {n}\n"}}} for n in range(1, 6)
        ]
        fake_rg(bin_dir, events)
        payload = run_program(bin_dir, GREP_PROGRAM, [json.dumps({"path": str(root), "pattern": "x", "limit": 2}), 51200, 500])
        assert payload["lines"] == ["a.txt:1: hit 1", "a.txt:2: hit 2"]
        assert payload["limit_reached"] is True

    def test_truncates_long_lines(self, tmp_path, bin_dir):
        root = tmp_path / "work"
        root.mkdir()
        events = [{"type": "match", "data": {"path": {"text": "a.txt"}, "line_number": 1, "lines": {"text": "z" * 600 + "\n"}}}]
        fake_rg(bin_dir, events)
        payload = run_program(bin_dir, GREP_PROGRAM, [json.dumps({"path": str(root), "pattern": "x", "limit": 100}), 51200, 500])
        assert payload["lines_truncated"] is True
        assert payload["lines"][0].endswith("... [truncated]")

    def test_reports_a_missing_binary(self, tmp_path, bin_dir):
        payload = run_program(bin_dir, GREP_PROGRAM, [json.dumps({"path": str(tmp_path), "pattern": "x", "limit": 1}), 51200, 500])
        assert payload == {"ok": False, "error": "sandbox image has no ripgrep (rg)"}

    def test_reports_an_rg_failure(self, tmp_path, bin_dir):
        fake_tool(bin_dir, "rg", "import sys\nsys.stderr.write('regex parse error\\n')\nsys.exit(2)")
        payload = run_program(bin_dir, GREP_PROGRAM, [json.dumps({"path": str(tmp_path), "pattern": "(", "limit": 100}), 51200, 500])
        assert payload["ok"] is False and payload["error"] == "regex parse error"


class TestFindProgram:
    def test_relativizes_results(self, tmp_path, bin_dir):
        root = tmp_path / "work"
        (root / "sub").mkdir(parents=True)
        fake_tool(bin_dir, "fd", f"print({str(root / 'one.md')!r})\nprint({str(root / 'sub' / 'two.md')!r})")
        payload = run_program(bin_dir, FIND_PROGRAM, [json.dumps({"path": str(root), "pattern": "*.md", "limit": 1000}), 51200])
        assert payload["ok"] is True and payload["lines"] == ["one.md", "sub/two.md"]

    def test_stops_past_the_limit(self, tmp_path, bin_dir):
        root = tmp_path / "work"
        root.mkdir()
        fake_tool(bin_dir, "fd", "".join(f"print({str(root / f'{n}.md')!r})\n" for n in range(1, 5)))
        payload = run_program(bin_dir, FIND_PROGRAM, [json.dumps({"path": str(root), "pattern": "*.md", "limit": 2}), 51200])
        assert payload["lines"] == ["1.md", "2.md"] and payload["limit_reached"] is True

    def test_falls_back_to_fdfind(self, tmp_path, bin_dir):
        root = tmp_path / "work"
        root.mkdir()
        fake_tool(bin_dir, "fdfind", f"print({str(root / 'one.md')!r})")
        payload = run_program(bin_dir, FIND_PROGRAM, [json.dumps({"path": str(root), "pattern": "*.md", "limit": 10}), 51200])
        assert payload["lines"] == ["one.md"]

    def test_reports_a_missing_binary(self, tmp_path, bin_dir):
        payload = run_program(bin_dir, FIND_PROGRAM, [json.dumps({"path": str(tmp_path), "pattern": "*", "limit": 1}), 51200])
        assert payload == {"ok": False, "error": "sandbox image has no fd"}


class TestFormatting:
    def test_ls_renders_entries_and_notices(self):
        broker = FakeBroker(stdout=json.dumps({"ok": True, "entries": ["a/", "b.txt"], "limit_reached": True}))
        output = asyncio.run(list_directory(broker, "lease", "/workspace/work", limit=2))
        assert output.startswith("a/\nb.txt")
        assert "[2 entries limit reached. Use limit=4 for more]" in output
        assert broker.calls[0]["timeout"] == workspace_query.QUERY_TIMEOUT_SECONDS

    def test_empty_directory_is_spelled_out(self):
        broker = FakeBroker(stdout=json.dumps({"ok": True, "entries": [], "limit_reached": False}))
        assert asyncio.run(list_directory(broker, "lease", "/workspace/work")) == "(empty directory)"

    def test_grep_notices_cover_all_three_limits(self):
        broker = FakeBroker(
            stdout=json.dumps(
                {
                    "ok": True,
                    "lines": ["a.txt:1: x"],
                    "limit_reached": True,
                    "bytes_truncated": True,
                    "lines_truncated": True,
                }
            )
        )
        output, _ = asyncio.run(search_text(broker, "lease", "x", "/workspace/work", limit=100))
        assert "100 matches limit reached. Use limit=200 for more, or refine pattern" in output
        assert "50.0KB limit reached" in output
        assert "Some lines truncated to 500 chars. Use read tool to see full lines" in output

    def test_no_matches_says_what_was_searched(self):
        # A bare "no matches" reads like an unfinished call and makes models re-check with bash.
        broker = FakeBroker(stdout=json.dumps({"ok": True, "lines": [], "limit_reached": False}))
        text, matches = asyncio.run(search_text(broker, "lease", "x", "/workspace/work"))
        assert text == 'No matches found for "x" under /workspace/work' and matches == 0
        text, files = asyncio.run(find_files(broker, "lease", "*.md", "/workspace/work"))
        assert text == 'No files found matching "*.md" under /workspace/work' and files == 0

    def test_a_missing_binary_becomes_a_readable_message(self):
        broker = FakeBroker(stdout=json.dumps({"ok": False, "error": "sandbox image has no ripgrep (rg)"}))
        with pytest.raises(QueryError, match="沙箱镜像缺少搜索工具：rg"):
            asyncio.run(search_text(broker, "lease", "x", "/workspace/work"))

    def test_a_failed_exec_surfaces_its_last_stderr_line(self):
        broker = FakeBroker(exit_code=1, stderr="boom\nlast line\n")
        with pytest.raises(QueryError, match="last line"):
            asyncio.run(list_directory(broker, "lease", "/workspace/work"))

    def test_unparsable_output_is_reported(self):
        broker = FakeBroker(stdout="not json")
        with pytest.raises(QueryError, match="沙箱查询没有返回可用的结果"):
            asyncio.run(list_directory(broker, "lease", "/workspace/work"))


def test_grep_keeps_a_leading_dot_glob_and_ignores_like_find(tmp_path, bin_dir):
    """``lstrip("./")`` ate the dot of ".env"/".github"; rg also needs --no-require-git to match fd."""
    root = tmp_path / "work"
    root.mkdir()
    marker = tmp_path / "argv.json"
    fake_tool(bin_dir, "rg", f"import json, sys, pathlib\npathlib.Path({str(marker)!r}).write_text(json.dumps(sys.argv))")
    for glob in (".env*", ".github/**/*.yml"):
        run_program(bin_dir, GREP_PROGRAM, [json.dumps({"path": str(root), "pattern": "x", "limit": 10, "glob": glob}), 51200, 500])
        argv = json.loads(marker.read_text())
        assert argv[argv.index("--glob") + 1] == glob
        assert "--no-require-git" in argv
