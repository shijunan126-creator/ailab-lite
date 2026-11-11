"""The renamed workspace tools, exercised through ``execute_tool``."""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from ailab_platform.agent.tools import SANDBOX_TOOLS, ToolError, execute_tool, tools_for_chat, tools_for_turn
from ailab_platform.clients import ClientError

WORK = "/workspace/work"


class FakeBroker:
    """Files in a dict; ``exec`` returns one canned program result."""

    def __init__(self, files: dict[str, bytes] | None = None, stdout: str = "", exit_code: int = 0) -> None:
        self.files = dict(files or {})
        self.stdout, self.exit_code = stdout, exit_code
        self.commands: list[str] = []

    async def get_file(self, lease_id: str, path: str) -> bytes:
        if path not in self.files:
            raise ClientError("GET", f"/internal/leases/{lease_id}/files", 400, "文件不存在")
        return self.files[path]

    async def put_file(self, lease_id: str, path: str, data: bytes) -> None:
        self.files[path] = data

    async def exec(self, lease_id: str, command: str, timeout: int | None = None) -> dict:
        self.commands.append(command)
        return {"exit_code": self.exit_code, "stdout": self.stdout, "stderr": "", "duration_ms": 1}


def call(name: str, args: dict, broker: FakeBroker) -> dict:
    return asyncio.run(execute_tool(name, args, "lease", broker))


def test_the_catalog_offers_the_pi_names():
    work = {tool["function"]["name"] for tool in tools_for_turn(2)}
    assert {"read", "write", "edit", "ls", "bash", "bash_wait", "bash_kill", "grep", "find"} <= work
    assert not work & {"read_file", "write_file", "list_files", "exec", "exec_wait", "exec_kill"}
    # Chat has no sandbox, so it gets none of them.
    assert not {tool["function"]["name"] for tool in tools_for_chat(2)} & {"read", "write", "edit", "ls", "grep", "find", "bash"}
    assert {"read", "write", "edit", "ls", "grep", "find", "bash"} <= SANDBOX_TOOLS


def test_write_then_read_round_trip():
    broker = FakeBroker()
    written = call("write", {"path": f"{WORK}/a.txt", "content": "第一行\n第二行\n"}, broker)
    assert written["ok"] is True and written["bytes"] == len("第一行\n第二行\n".encode())
    read = call("read", {"path": f"{WORK}/a.txt"}, broker)
    assert read["content"] == "第一行\n第二行\n" and read["total_lines"] == 3
    assert "next_offset" not in read


def test_read_windows_by_line_and_reports_the_next_offset():
    broker = FakeBroker({f"{WORK}/a.txt": "\n".join(f"line {index}" for index in range(1, 101)).encode()})
    read = call("read", {"path": f"{WORK}/a.txt", "offset": 41, "limit": 20}, broker)
    assert read["content"].startswith("line 41\n")
    assert read["next_offset"] == 61
    assert read["content"].endswith("[40 more lines in file. Use offset=61 to continue.]")


def test_read_rejects_a_missing_file_and_bad_arguments():
    broker = FakeBroker()
    with pytest.raises(ToolError, match=re.escape("Could not read file: /workspace/work/none.txt. Error code: ENOENT.")):
        call("read", {"path": f"{WORK}/none.txt"}, broker)
    with pytest.raises(ToolError, match="offset 须是正整数"):
        call("read", {"path": f"{WORK}/a.txt", "offset": 0}, broker)


def test_read_refuses_binary_and_paths_outside_the_workspace():
    broker = FakeBroker({f"{WORK}/a.png": b"\x89PNG\x00binary"})
    result = call("read", {"path": f"{WORK}/a.png"}, broker)
    assert result["ok"] is False and "view_workspace_file" in result["error"]
    with pytest.raises(ToolError):
        call("read", {"path": "/etc/passwd"}, broker)


def test_edit_replaces_and_writes_the_file():
    broker = FakeBroker({f"{WORK}/a.txt": b"first\nsecond\nthird\n"})
    result = call("edit", {"path": f"{WORK}/a.txt", "edits": [{"oldText": "second\n", "newText": "SECOND\n"}]}, broker)
    assert result == {"ok": True, "path": f"{WORK}/a.txt", "replaced": 1}
    assert broker.files[f"{WORK}/a.txt"] == b"first\nSECOND\nthird\n"


def test_edit_keeps_crlf_and_reports_a_refused_edit():
    broker = FakeBroker({f"{WORK}/a.txt": b"first\r\nsecond\r\n"})
    call("edit", {"path": f"{WORK}/a.txt", "edits": [{"oldText": "second\n", "newText": "SECOND\n"}]}, broker)
    assert broker.files[f"{WORK}/a.txt"] == b"first\r\nSECOND\r\n"
    with pytest.raises(ToolError, match="Found 2 occurrences"):
        call(
            "edit",
            {"path": f"{WORK}/b.txt", "edits": [{"oldText": "x", "newText": "y"}]},
            FakeBroker({f"{WORK}/b.txt": b"x\nx\n"}),
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"edits": '[{"oldText": "a", "newText": "A"}]'},  # a JSON string where the schema says array (GLM does this)
        {"edits": {"oldText": "a", "newText": "A"}},  # one object instead of a one-element array
        {"edits": '[{"oldText": "a", "newText": "A"}, {"oldText": "b", "newText": "B"}]'},
    ],
)
def test_edit_accepts_the_shapes_models_actually_send(arguments):
    broker = FakeBroker({f"{WORK}/a.txt": b"a\nb\n"})
    result = call("edit", {"path": f"{WORK}/a.txt", **arguments}, broker)
    assert result["ok"] is True
    assert broker.files[f"{WORK}/a.txt"] in {b"A\nb\n", b"A\nB\n"}


def test_edit_accepts_legacy_top_level_old_text():
    broker = FakeBroker({f"{WORK}/a.txt": b"a\n"})
    result = call("edit", {"path": f"{WORK}/a.txt", "oldText": "a", "newText": "A"}, broker)
    assert result == {"ok": True, "path": f"{WORK}/a.txt", "replaced": 1}
    assert broker.files[f"{WORK}/a.txt"] == b"A\n"


def test_edit_rejects_a_payload_it_cannot_read():
    with pytest.raises(ToolError, match="edit 需要至少一项 edits"):
        call("edit", {"path": f"{WORK}/a.txt", "edits": "not json"}, FakeBroker({f"{WORK}/a.txt": b"a\n"}))
    with pytest.raises(ToolError, match="字符串 oldText 和 newText"):
        call("edit", {"path": f"{WORK}/a.txt", "edits": [{"oldText": ["a"]}]}, FakeBroker({f"{WORK}/a.txt": b"a\n"}))


def test_edit_reports_a_missing_file_and_a_bad_payload():
    with pytest.raises(ToolError, match=re.escape("Could not edit file: /workspace/work/gone.txt. Error code: ENOENT.")):
        call("edit", {"path": f"{WORK}/gone.txt", "edits": [{"oldText": "a", "newText": "b"}]}, FakeBroker())
    with pytest.raises(ToolError, match=re.escape("edit 需要至少一项 edits")):
        call("edit", {"path": f"{WORK}/a.txt", "edits": []}, FakeBroker())
    with pytest.raises(ToolError, match="字符串 oldText 和 newText"):
        call("edit", {"path": f"{WORK}/a.txt", "edits": [{"oldText": 1}]}, FakeBroker())


def test_two_edits_of_one_file_do_not_interleave():
    broker = FakeBroker({f"{WORK}/a.txt": b"a\nb\n"})

    async def both():
        return await asyncio.gather(
            execute_tool("edit", {"path": f"{WORK}/a.txt", "edits": [{"oldText": "a", "newText": "A"}]}, "lease", broker),
            execute_tool("edit", {"path": f"{WORK}/a.txt", "edits": [{"oldText": "b", "newText": "B"}]}, "lease", broker),
        )

    assert all(result["ok"] for result in asyncio.run(both()))
    assert broker.files[f"{WORK}/a.txt"] == b"A\nB\n"


def test_ls_passes_the_directory_and_formats_the_listing():
    broker = FakeBroker(stdout=json.dumps({"ok": True, "entries": ["assets/", "work/"], "limit_reached": False}))
    result = call("ls", {"path": WORK}, broker)
    assert result["output"] == "assets/\nwork/"
    assert "/workspace/work" in broker.commands[0]


def test_ls_defaults_to_the_workspace_root():
    broker = FakeBroker(stdout=json.dumps({"ok": True, "entries": [], "limit_reached": False}))
    result = call("ls", {}, broker)
    assert result == {"ok": True, "path": "/workspace", "output": "(empty directory)"}


def test_grep_and_find_require_a_pattern_and_format_their_output():
    broker = FakeBroker(stdout=json.dumps({"ok": True, "lines": ["a.txt:1: hit"], "limit_reached": False}))
    assert call("grep", {"pattern": "hit"}, broker)["output"] == "a.txt:1: hit"
    assert call("find", {"pattern": "*.md"}, FakeBroker(stdout=json.dumps({"ok": True, "lines": ["a.md"]})))["output"] == "a.md"
    with pytest.raises(ToolError, match="grep requires pattern"):
        call("grep", {}, broker)
    with pytest.raises(ToolError, match="find requires pattern"):
        call("find", {}, broker)


def test_a_sandbox_query_failure_becomes_a_tool_error():
    broker = FakeBroker(exit_code=1)
    with pytest.raises(ToolError, match="沙箱查询失败"):
        call("ls", {"path": WORK}, broker)


@pytest.mark.parametrize(
    "catalog",
    [lambda: tools_for_turn(1), lambda: tools_for_turn(2), lambda: tools_for_chat(1), lambda: tools_for_chat(2)],
)
def test_the_catalog_never_offers_the_same_tool_twice(catalog):
    # A duplicate schema is a request the provider may reject; one tool, one name.
    names = [tool["function"]["name"] for tool in catalog()]
    assert len(names) == len(set(names)), names


def test_the_wait_tool_asks_for_one_wait_parameter_name():
    wait = next(tool for tool in tools_for_turn(2) if tool["function"]["name"] == "bash_wait")
    assert set(wait["function"]["parameters"]["properties"]) == {"handle", "wait_seconds", "offset"}


def test_two_concurrent_edits_of_one_file_both_land():
    """run_subtasks runs 2-4 children at once; without the queue the second read-modify-write wins."""
    import time

    class SlowBroker(FakeBroker):
        async def get_file(self, lease, path):
            data = await super().get_file(lease, path)
            await asyncio.sleep(0.05)  # widen the window between read and write
            return data

    broker = SlowBroker({f"{WORK}/a.md": b"## a\n\n## b\n"})

    async def scenario():
        return await asyncio.gather(
            execute_tool("edit", {"path": f"{WORK}/a.md", "edits": [{"oldText": "## a", "newText": "## a\n\nx"}]}, "lease", broker),
            execute_tool("edit", {"path": f"{WORK}/a.md", "edits": [{"oldText": "## b", "newText": "## b\n\ny"}]}, "lease", broker),
        )

    started = time.monotonic()
    results = asyncio.run(scenario())
    assert [r["ok"] for r in results] == [True, True]
    assert broker.files[f"{WORK}/a.md"] == b"## a\n\nx\n\n## b\n\ny\n"
    assert time.monotonic() - started >= 0.1  # serialized, not interleaved
