"""Office module reads are complete, bounded, mode-safe and evidence-based."""

import asyncio
import copy
import hashlib
import json

import pytest

from ailab_platform.agent.loop import AgentLoop, _Turn
from ailab_platform.agent.skill_modules import (
    ModuleVisibility,
    annotate_summary,
    document_guides,
    identity,
    read_module,
    visible_modules,
)
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
        assert "cursor" not in result and "next_cursor" not in result
        # The guide may restate a resource; the bytes must still be exactly what the model is served.
        overlay = document_guides()["formats"][name.removeprefix("platform.")][mode]
        adapted = {"text": overlay["viewer"]} if module["path"] == "viewer/SKILL.md" else overlay.get("resources", {}).get(module["path"])
        assert result["skill"] == (adapted["text"] if adapted is not None else (root / module["path"]).read_text())


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
        from ailab_platform.adapters.runtime import fresh_state
        from ailab_platform.agent.context_band import ContextBand, admit_tool_result, batch_budget

        loop = AgentLoop(platform=FakePlatform(), broker=FakeBroker())
        turn = _Turn({"model": "m", "options": {"mode": "chat"}, "_fixed_catalog": [LOAD_SKILL]})
        turn.messages = [{"role": "system", "content": "rules"}, {"role": "system", "content": "context"}]
        state = {**fresh_state(), "messages": turn.messages}
        loop._states["r"] = state
        arguments = {"name": "platform.docx", "resource": "viewer/SKILL.md"}
        calls = [ToolCall("first", "load_skill", arguments), ToolCall("second", "load_skill", arguments)]
        turn.messages.append(
            {
                "role": "assistant",
                "tool_calls": [{"id": call.id, "function": {"name": call.name, "arguments": json.dumps(call.arguments)}} for call in calls],
            }
        )
        first = await loop._run_tool(turn, "r", calls[0], 0)
        assert first["ok"] and first["skill"]
        turn.messages.append(tool_result_message("first", "load_skill", first))
        # A second complete file would exceed the new pending-batch budget.
        oversized = {**first, "skill": "大" * batch_budget(ContextBand.for_model(spec), turn.messages, [LOAD_SKILL], state)}
        refused = admit_tool_result(ContextBand.for_model(spec), turn.messages, [LOAD_SKILL], state, calls[1], oversized)
        assert not refused["ok"] and "cannot fit" in refused["error"] and "skill" not in refused
        turn.messages.append(tool_result_message("second", "load_skill", refused))
        third = ToolCall("third", "load_skill", arguments)
        turn.messages.append(
            {"role": "assistant", "tool_calls": [{"id": third.id, "function": {"name": third.name, "arguments": json.dumps(arguments)}}]}
        )
        duplicate = await loop._run_tool(turn, "r", third, 0)
        assert duplicate["already_loaded"] and "skill" not in duplicate
        turn.messages.append(tool_result_message(third.id, third.name, duplicate))
        turn.messages[:] = [{"role": "system", "content": "rules"}, {"role": "system", "content": "context"}]
        await loop._event("r", "agent.context_boundary", {"reason": "compacted"})
        fourth = ToolCall("fourth", "load_skill", arguments)
        turn.messages.append(
            {"role": "assistant", "tool_calls": [{"id": fourth.id, "function": {"name": fourth.name, "arguments": json.dumps(arguments)}}]}
        )
        reread = await loop._run_tool(turn, "r", fourth, 0)
        assert reread["skill"] == first["skill"]

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
        from ailab_platform.adapters.runtime import fresh_state

        loop._states["r"] = {**fresh_state(), "messages": turn.messages}
        result = await loop._run_tool(turn, "r", ToolCall("read", "load_skill", {"name": "platform.docx", "resource": resource}), 0)
        assert not result["ok"] and "cannot fit" in result["error"] and "skill" not in result

    asyncio.run(exercise())


def test_legacy_skill_visibility_also_survives_only_with_paired_body():
    async def exercise():
        loop = AgentLoop(platform=FakePlatform(), broker=FakeBroker())
        turn = _Turn({"options": {"mode": "work"}, "_fixed_catalog": [LOAD_SKILL]})
        turn.messages = []
        from ailab_platform.adapters.runtime import fresh_state

        loop._states["r"] = {**fresh_state(), "messages": turn.messages}
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


@pytest.mark.parametrize("old_version", [None, "1.1.0"])
def test_conversation_loaded_before_a_package_update_switches_to_the_installed_package(old_version):
    """A deploy replaces the package; the earlier text stays in history and the next read says it is superseded."""

    async def exercise():
        from ailab_platform.adapters.runtime import fresh_state
        from ailab_platform.agent.loop import SKILL_PACKAGE_UPDATED

        loop = AgentLoop(platform=FakePlatform(), broker=FakeBroker())
        turn = _Turn({"options": {"mode": "work"}, "_fixed_catalog": [LOAD_SKILL]})
        earlier = {**read_module("platform.docx", "work", "routes/edit.md"), "package_hash": "0" * 64}
        if old_version:
            earlier["version"] = old_version
        earlier["guide_revision"] = "1" * 64
        turn.messages = [{"role": "system", "content": "rules"}, *paired(earlier, "earlier")]
        prefix = copy.deepcopy(turn.messages)
        loop._states["r"] = {**fresh_state(), "messages": turn.messages}
        loop._register_carried_skills(turn, "r")

        async def read(call_id, **arguments):
            call = ToolCall(call_id, "load_skill", {"name": "platform.docx", **arguments})
            result = await loop._run_tool(turn, "r", call, 0)
            turn.messages.extend(paired({**result, "name": "platform.docx", **arguments}, call_id)[:1])
            turn.messages.append(tool_result_message(call_id, "load_skill", result))
            return result

        navigation = await read("nav")
        assert navigation["ok"] and navigation["skill"] and navigation["note"] == SKILL_PACKAGE_UPDATED
        assert navigation["package_hash"] == catalog()["platform.docx"]["sha256"]
        # The text read before the update is not "already shown": it is the old package's.
        resource = await read("edit", resource="routes/edit.md")
        assert resource["ok"] and resource["skill"] and "note" not in resource and not resource.get("already_loaded")
        assert (await read("again", resource="routes/edit.md"))["already_loaded"]
        assert turn.messages[: len(prefix)] == prefix
        stale = await loop._run_tool(turn, "r", ToolCall("old", "load_skill", {"name": "platform.docx", "version": "1.1.0"}), 0)
        assert not stale["ok"] and "1.2.0" in stale["error"]

    asyncio.run(exercise())
