"""Office module reads are complete, bounded, mode-safe and evidence-based."""

import asyncio
import copy
import hashlib
import json

import pytest

from ailab_platform.agent.loop import AgentLoop, _Turn
from ailab_platform.agent.skill_modules import ModuleVisibility, annotate_summary, identity, read_module, visible_modules
from ailab_platform.agent.tools import LOAD_SKILL, tool_result_message, tools_for_chat, tools_for_turn
from ailab_platform.catalog import Catalog, ModelSpec
from ailab_platform.contracts.context_summary import ContextSummary
from ailab_platform.llm import ToolCall
from ailab_platform.model_policy import estimator
from ailab_platform.skill_registry import BASE_SKILLS, catalog
from tests.test_agent_loop import FakeBroker, FakePlatform


def paired(result, call_id="read"):
    arguments = {"name": result["name"]}
    if result.get("resource"):
        arguments.update(resource=result["resource"])
    return [
        {"role": "assistant", "tool_calls": [{"id": call_id, "function": {"name": "load_skill", "arguments": json.dumps(arguments)}}]},
        tool_result_message(call_id, "load_skill", result),
    ]


@pytest.mark.parametrize("mode", ["chat", "work"])
@pytest.mark.parametrize("name", BASE_SKILLS)
def test_navigation_and_whole_file_reads(name, mode):
    navigation = read_module(name, mode)
    root = catalog()[name]["path"].parent
    for module in json.loads((root / "modules.json").read_text())["modules"]:
        if mode not in module["modes"]:
            assert module["path"] not in navigation["skill"]
            with pytest.raises(ValueError, match="unavailable"):
                read_module(name, mode, module["path"])
            continue
        assert module["path"] in navigation["skill"]
        result = read_module(name, mode, module["path"])
        assert result["complete"] and estimator.raw(result["skill"]) == result["estimated_tokens"]
        assert hashlib.sha256(result["skill"].encode()).hexdigest() == result["hash"]
        assert result["skill"] == (root / module["path"]).read_text()
        assert "cursor" not in result and "next_cursor" not in result


@pytest.mark.parametrize("resource", ["/etc/passwd", "../SKILL.md", "absent"])
def test_invalid_reads_never_fall_back(resource):
    with pytest.raises(ValueError):
        read_module("platform.docx", "work", resource)


def test_visibility_requires_paired_complete_current_text():
    result = read_module("platform.docx", "work", "viewer/SKILL.md")
    messages = paired(result)
    original = copy.deepcopy(messages)
    assert identity(result) in visible_modules(messages)
    for bad in (
        messages[1:],
        [{**messages[0], "role": "user"}, messages[1]],
        paired({**result, "ok": False}),
        paired({**result, "skill": result["skill"][:-1]}),
        paired({**result, "complete": False}),
        [{"role": "system", "content": annotate_summary("summary", messages)}],
    ):
        assert not visible_modules(bad)
    view = ModuleVisibility()
    assert identity(result) in view.read(messages)
    messages[:] = [{"role": "system", "content": "summary"}]
    assert not view.read(messages)
    messages.extend(paired(result))
    assert identity(result) in view.read(messages)
    assert original == paired(result)
    structured = annotate_summary(ContextSummary().model_dump_json(), original, structured=True)
    assert ContextSummary.model_validate_json(structured).read_modules[0].hash == result["hash"]


def test_fixed_schema_in_both_modes():
    for tools in (tools_for_chat(), tools_for_turn()):
        assert next(t for t in tools if t["function"]["name"] == "load_skill") == LOAD_SKILL


def test_budget_refusal_and_group_cumulative_admission(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=12000, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))

    async def exercise():
        loop = AgentLoop(platform=FakePlatform(), broker=FakeBroker())
        turn = _Turn({"model": "m", "options": {"mode": "chat"}, "_fixed_catalog": [LOAD_SKILL]})
        turn.messages = [{"role": "system", "content": "rules"}]
        loop._states["r"] = {"messages": turn.messages}
        arguments = {"name": "platform.docx", "resource": "viewer/SKILL.md"}
        first = await loop._run_tool(turn, "r", ToolCall("first", "load_skill", arguments), 0)
        assert first["ok"] and first["skill"]
        # Another result in the same pending group consumes the shared window.
        turn.messages.append(tool_result_message("other", "read_file", {"text": "大" * 10500}))
        refused = await loop._run_tool(turn, "r", ToolCall("second", "load_skill", arguments), 0)
        assert not refused["ok"] and "cannot fit" in refused["error"] and "skill" not in refused
        turn.messages[:] = paired(first)
        duplicate = await loop._run_tool(turn, "r", ToolCall("third", "load_skill", arguments), 0)
        assert duplicate["already_loaded"] and "skill" not in duplicate
        # A boundary can replace the prefix without changing list length or the last object.
        turn.messages[:] = [{"role": "system", "content": "summary"}, turn.messages[-1]]
        await loop._event("r", "agent.context_boundary", {"reason": "compacted"})
        reread = await loop._run_tool(turn, "r", ToolCall("fourth", "load_skill", arguments), 0)
        assert reread["skill"] == first["skill"]  # installed state cannot suppress a read
        error = await loop._run_tool(turn, "r", ToolCall("bad", "load_skill", {"name": "platform.nope"}), 0)
        assert not error["ok"]

    asyncio.run(exercise())


def test_package_drift_and_symlinks_refuse_reads(tmp_path, monkeypatch):
    import shutil

    from ailab_platform.agent import skill_modules

    original = catalog()["platform.docx"]
    root = tmp_path / "platform.docx"
    shutil.copytree(original["path"].parent, root)
    monkeypatch.setattr(skill_modules, "catalog", lambda: {"platform.docx": {**original, "path": root / "SKILL.md"}})
    path = root / "viewer/SKILL.md"
    path.write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        read_module("platform.docx", "work", "viewer/SKILL.md")
    path.unlink()
    path.symlink_to(original["path"].parent / "viewer/SKILL.md")
    with pytest.raises(ValueError, match="invalid resource path"):
        read_module("platform.docx", "work", "viewer/SKILL.md")


def test_large_file_is_whole_or_refused_by_small_window(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=2000, max_output_tokens=256, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    manifest = json.loads((catalog()["platform.docx"]["path"].parent / "modules.json").read_text())
    resource = next(m["path"] for m in manifest["modules"] if m["estimated_tokens"] > 2048)
    module = read_module("platform.docx", "work", resource)
    assert module["estimated_tokens"] > 2048
    assert module["skill"].endswith("\n")

    async def exercise():
        loop = AgentLoop(platform=FakePlatform(), broker=FakeBroker())
        turn = _Turn(
            {"model": "m", "options": {"mode": "work"}, "context_settings": {"auto_compact": False}, "_fixed_catalog": [LOAD_SKILL]}
        )
        turn.messages = [{"role": "system", "content": "rules"}]
        loop._states["r"] = {"messages": turn.messages}
        result = await loop._run_tool(turn, "r", ToolCall("read", "load_skill", {"name": "platform.docx", "resource": resource}), 0)
        assert not result["ok"] and "cannot fit" in result["error"] and "skill" not in result

    asyncio.run(exercise())


def test_legacy_skill_visibility_also_survives_only_with_paired_body():
    async def exercise():
        loop = AgentLoop(platform=FakePlatform(), broker=FakeBroker())
        turn = _Turn({"options": {"mode": "work"}, "_fixed_catalog": [LOAD_SKILL]})
        turn.messages = []
        loop._states["r"] = {"messages": turn.messages}
        call = ToolCall("one", "load_skill", {"name": "pptx"})
        first = await loop._run_tool(turn, "r", call, 0)
        assert first["ok"] and first["skill"]
        turn.messages.extend(
            [
                {"role": "assistant", "tool_calls": [{"id": "one", "function": {"name": "load_skill", "arguments": '{"name":"pptx"}'}}]},
                tool_result_message("one", "load_skill", first),
            ]
        )
        assert (await loop._run_tool(turn, "r", call, 0))["already_loaded"]
        invalid = await loop._run_tool(turn, "r", ToolCall("bad", "load_skill", {"name": "pptx", "resource": "missing"}), 0)
        assert not invalid["ok"]
        turn.messages[:] = [{"role": "system", "content": "summary"}]
        assert (await loop._run_tool(turn, "r", call, 0))["skill"] == first["skill"]

    asyncio.run(exercise())


def test_unknown_package_is_an_explicit_error():
    with pytest.raises(ValueError, match="unknown skill"):
        read_module("platform.missing", "work")


def test_chat_cannot_read_work_review_role_with_shell_setup():
    with pytest.raises(ValueError, match="unavailable"):
        read_module("platform.docx", "chat", "agents/visual-judge.md")
