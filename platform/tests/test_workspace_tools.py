"""The renamed workspace tools, exercised through ``execute_tool``."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime

import pytest

from ailab_platform.agent.tools import (
    READ_WINDOW_BYTES,
    SANDBOX_TOOLS,
    ToolContext,
    ToolError,
    execute_tool,
    local_time,
    tools_for_chat,
    tools_for_turn,
)
from ailab_platform.clients import ClientError
from tests.runtime_fakes import sandbox_window

WORK = "/workspace/work"


class FakeBroker:
    """Files in a dict; ``exec`` returns one canned program result."""

    def __init__(self, files: dict[str, bytes] | None = None, stdout: str = "", exit_code: int = 0) -> None:
        self.files = dict(files or {})
        self.stdout, self.exit_code = stdout, exit_code
        self.commands: list[str] = []

    async def get_file(self, lease_id: str, path: str, *, line: int | None = None, length: int | None = None, **kwargs) -> bytes:
        if path not in self.files:
            raise ClientError("GET", f"/internal/leases/{lease_id}/files", 404, '{"detail": "no such file in the sandbox"}')
        return sandbox_window(self.files[path], line, length)

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


def test_current_time_answers_in_the_runs_zone():
    """The tool must not start a sandbox: the browser's zone arrives frozen on the run."""
    moment = datetime(2026, 11, 13, 6, 30, tzinfo=UTC)
    assert local_time("Asia/Shanghai", moment) == {
        "ok": True,
        "date": "2026-11-13",
        "weekday": "周五",
        "time": "14:30",
        "timezone": "Asia/Shanghai",
    }
    # An old run without a zone answers in UTC and names it, instead of implying a local day.
    assert local_time("", moment) == {"ok": True, "date": "2026-11-13", "weekday": "周五", "time": "06:30", "timezone": "UTC"}
    assert local_time("Mars/Olympus", moment)["timezone"] == "UTC"
    from zoneinfo import ZoneInfo

    ctx = ToolContext(timezone="America/New_York")
    answer = asyncio.run(execute_tool("current_time", {}, "", FakeBroker(), ctx))
    local = datetime.now(UTC).astimezone(ZoneInfo("America/New_York"))
    assert answer == {
        "ok": True,
        "date": local.date().isoformat(),
        "weekday": "周" + "一二三四五六日"[local.weekday()],
        "time": local.strftime("%H:%M"),
        "timezone": "America/New_York",
    }


def test_current_time_is_offered_in_both_modes_with_no_arguments():
    for catalog in (tools_for_turn(1), tools_for_turn(2), tools_for_chat(1), tools_for_chat(2)):
        tool = next(t for t in catalog if t["function"]["name"] == "current_time")
        assert tool["function"]["parameters"] == {"type": "object", "properties": {}}


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
        async def get_file(self, lease, path, **kwargs):
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


def test_a_write_and_an_edit_of_one_file_cannot_interleave(monkeypatch):
    """Without the queue the edit reads the old content, the write lands, and the edit then writes its own copy back."""

    class SlowBroker(FakeBroker):
        async def get_file(self, lease_id, path, **kwargs):
            data = await super().get_file(lease_id, path)
            await asyncio.sleep(0.05)  # widen the read-modify-write window
            return data

    broker = SlowBroker({f"{WORK}/a.md": b"## a\n"})

    async def scenario():
        edit = asyncio.create_task(
            execute_tool("edit", {"path": f"{WORK}/a.md", "edits": [{"oldText": "## a", "newText": "## a\n\nx"}]}, "lease", broker)
        )
        await asyncio.sleep(0.01)  # the edit is already reading when the write starts
        await execute_tool("write", {"path": f"{WORK}/a.md", "content": "REPLACED\n"}, "lease", broker)
        return await edit

    result = asyncio.run(scenario())
    assert result["ok"] is True
    assert broker.files[f"{WORK}/a.md"] == b"REPLACED\n"  # the whole-file write is the last one to land


def test_a_too_large_file_is_not_reported_as_missing():
    """12 MB: the model must not hear ENOENT and rewrite the file with write."""

    class BigBroker(FakeBroker):
        async def get_file(self, lease_id, path, **kwargs):
            raise ClientError("GET", "/internal/leases/lease/files", 413, '{"detail": "file is 12582912 bytes; the limit is 10485760"}')

    with pytest.raises(ToolError, match="超过 10MB 读取上限"):
        call("read", {"path": f"{WORK}/big.csv"}, BigBroker())
    # edit has its own ceiling; the next action it offers is a script, not sed over a whole file.
    with pytest.raises(ToolError, match="超过 edit 上限 2 MB；用 bash 运行脚本修改"):
        call("edit", {"path": f"{WORK}/big.csv", "edits": [{"oldText": "a", "newText": "b"}]}, BigBroker())


def test_a_broker_400_hands_the_detail_to_the_model():
    class DirectoryBroker(FakeBroker):
        async def get_file(self, lease_id, path, **kwargs):
            raise ClientError("GET", "/internal/leases/lease/files", 400, '{"detail": "not a regular file: /workspace/work"}')

    with pytest.raises(ToolError, match="不是普通文件"):
        call("read", {"path": f"{WORK}/sub"}, DirectoryBroker())


def test_edit_refuses_a_file_over_its_own_memory_budget(monkeypatch):
    from ailab_platform.config import settings

    monkeypatch.setattr(settings, "max_edit_bytes", 16)
    with pytest.raises(ToolError, match="超过 edit 上限"):
        call("edit", {"path": f"{WORK}/a.md", "edits": [{"oldText": "a", "newText": "b"}]}, FakeBroker({f"{WORK}/a.md": b"a" * 32}))


def test_a_large_file_is_read_through_a_window_from_the_requested_line():
    """The Agent holds a 2 MiB window, not the 10 MiB the Broker would let it transfer."""
    seen: list[dict] = []

    class Windowed(FakeBroker):
        async def get_file(self, lease_id, path, **kwargs):
            seen.append(kwargs)
            return b"first line\n" + b"x" * READ_WINDOW_BYTES

    result = call("read", {"path": f"{WORK}/big.log"}, Windowed())
    assert seen == [{"line": 1, "length": READ_WINDOW_BYTES + 1}]
    assert result["ok"] and result["content"] == "first line\n\n[More lines in file. Use offset=2 to continue.]"
    assert result["next_offset"] == 2 and "total_lines" not in result  # the count is unknown, so it is not guessed


def test_a_complete_read_still_reports_the_line_count():
    result = call("read", {"path": f"{WORK}/a.txt"}, FakeBroker({f"{WORK}/a.txt": b"one\ntwo\n"}))
    assert result["total_lines"] == 3 and "partial" not in result  # a trailing newline counts an empty last line


def big_log(lines: int = 300_000) -> bytes:
    return b"".join(b"line %07d\n" % index for index in range(1, lines + 1))  # 13 bytes a line: ~3.7 MB


def test_an_offset_deep_in_a_big_file_is_read_where_it_is():
    """The sandbox seeks to the line, so the 2 MiB window no longer caps how far ``offset`` reaches."""
    broker = FakeBroker({f"{WORK}/big.log": big_log()})
    result = call("read", {"path": f"{WORK}/big.log", "offset": 100_000, "limit": 3}, broker)
    assert result["content"] == "line 0100000\nline 0100001\nline 0100002\n\n[More lines in file. Use offset=100003 to continue.]"
    assert result["next_offset"] == 100_003 and "total_lines" not in result


def test_the_tail_of_a_big_file_reports_its_exact_line_count():
    # From line 299 990 on, the window reaches the end of the file: the total is known again.
    result = call("read", {"path": f"{WORK}/big.log", "offset": 299_990}, FakeBroker({f"{WORK}/big.log": big_log()}))
    assert result["content"].startswith("line 0299990\n") and result["content"].endswith("line 0300000\n")
    assert result["total_lines"] == 300_001 and "next_offset" not in result  # Pi counts the empty line after the last newline


def test_the_window_edge_continues_instead_of_reading_as_end_of_file():
    """Every window ends with the offset of the next line, so the model can walk the whole file."""
    broker, offset = FakeBroker({f"{WORK}/big.log": big_log()}), 1
    for _ in range(5):  # the tail test above covers the end; five steps prove the windows meet
        result = call("read", {"path": f"{WORK}/big.log", "offset": offset}, broker)
        assert result["content"].startswith(f"line {offset:07d}\n")  # no line skipped, none repeated
        assert result["next_offset"] == offset + 2000 and "total_lines" not in result
        offset = result["next_offset"]


def test_an_offset_past_the_end_says_so():
    with pytest.raises(ToolError, match=re.escape("Offset 9 is beyond end of file (3 lines total)")):
        call("read", {"path": f"{WORK}/a.txt", "offset": 9}, FakeBroker({f"{WORK}/a.txt": b"a\nb\n"}))
    # Pi counts the empty line after a final newline; the window from that line is empty, which is not "past the end".
    assert call("read", {"path": f"{WORK}/a.txt", "offset": 3}, FakeBroker({f"{WORK}/a.txt": b"a\nb\n"}))["content"] == ""
    with pytest.raises(ToolError, match=re.escape("Offset 400000 is beyond end of file. 用 bash 的 wc -l")):
        call("read", {"path": f"{WORK}/big.log", "offset": 400_000}, FakeBroker({f"{WORK}/big.log": big_log()}))


def test_a_twelve_megabyte_file_is_read_through_the_window_not_refused():
    """It used to be a 413: the whole file was the unit of transfer, so a window could not exist."""
    big = b"header\n" + b"y" * (12 * 1024 * 1024)

    class Ranged(FakeBroker):
        async def get_file(self, lease_id, path, **kwargs):
            assert kwargs == {"line": 1, "length": READ_WINDOW_BYTES + 1}  # the Agent asks for a window, not the file
            return big[: READ_WINDOW_BYTES + 1]

    result = call("read", {"path": f"{WORK}/big.log"}, Ranged())
    assert result["ok"] and result["content"].startswith("header") and result["next_offset"] == 2
