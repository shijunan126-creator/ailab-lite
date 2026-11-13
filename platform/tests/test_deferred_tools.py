"""On-demand tools behind `call_tool`: the request never changes, a tool runs only after its description was shown."""

import asyncio
import json
from itertools import pairwise

from ailab_platform.agent import deferred
from ailab_platform.agent.loop import AgentLoop, _available_names, _deferred_tools, _tools_for_run
from ailab_platform.agent.tool_schema import parameters
from ailab_platform.contracts.document_input import ReadDocument
from ailab_platform.llm import ToolCall
from ailab_platform.model_transport import ModelTransport
from tests.prefix_harness import assert_extension, scripted_server
from tests.test_agent_loop import FakeBroker, FakePlatform

STEPS = {"steps": [{"id": "a", "title": "读数据", "status": "running"}]}


def _names(tools):
    return [t["function"]["name"] for t in tools]


def test_request_declares_frequent_tools_and_lists_the_rest_in_call_tool():
    for mode in ("work", "chat"):
        run = {"options": {"mode": mode, "client": "portal"}}
        declared = _names(_tools_for_run(run))
        on_demand = deferred.names(_tools_for_run(run))
        assert declared[-1] == deferred.CALL and on_demand == set(_deferred_tools(run))
        assert not on_demand & set(declared) and on_demand <= deferred.DEFERRED[mode]
        assert {"memory", "schedule", "run_js"} <= on_demand and on_demand <= _available_names(run)
        description = _tools_for_run(run)[-1]["function"]["description"]
        assert all(f"- {name}：" in description for name in on_demand)
    assert {"bash", "read", "deliver_file"} <= set(_names(_tools_for_run({"options": {"mode": "work"}})))


def test_a_frozen_catalog_keeps_its_on_demand_names():
    run = {"options": {"mode": "work"}}
    frozen = _tools_for_run(run)
    assert _tools_for_run({**run, "_fixed_catalog": frozen}) is frozen
    # a run frozen before on-demand tools existed declares everything and has none
    assert deferred.names([t for t in frozen if t["function"]["name"] != deferred.CALL]) == frozenset()


def test_call_tool_runs_only_after_the_description_was_shown():
    available = frozenset({"update_plan"})
    definitions = {"update_plan": {"function": {"name": "update_plan", "description": "记录计划", "parameters": {"type": "object"}}}}
    call = ToolCall("c1", deferred.CALL, {"name": "update_plan", "arguments": STEPS})
    assert deferred.resolve(call, available, []) is None  # not documented yet: describe instead of running
    shown = deferred.describe(call.arguments, definitions, [])
    assert shown["tool_doc"] == "update_plan" and shown["description"] == "记录计划"
    messages = [{"role": "tool", "tool_call_id": "c0", "content": json.dumps(shown, ensure_ascii=False)}]
    assert deferred.resolve(call, available, messages) == ToolCall("c1", "update_plan", STEPS)
    as_text = ToolCall("c2", deferred.CALL, {"name": "update_plan", "arguments": json.dumps(STEPS)})
    assert deferred.resolve(as_text, available, messages) == ToolCall("c2", "update_plan", STEPS)
    assert deferred.resolve(ToolCall("c3", deferred.CALL, {"name": "bash", "arguments": {}}), available, messages) is None
    assert deferred.describe({"name": "bash"}, definitions, messages)["ok"] is False
    assert "已在上文" in deferred.describe({"name": "update_plan"}, definitions, messages)["note"]


def test_compact_parameters_drop_validator_noise_only():
    full = ReadDocument.model_json_schema()
    compact = parameters(ReadDocument)
    text = json.dumps(compact)
    assert '"title"' not in text and "2147483647" not in text and '"maxLength"' not in text
    assert set(compact["properties"]) == set(full["properties"]) and compact["required"] == full["required"]
    assert compact["properties"]["text_source"]["enum"] == ["native", "ocr"]
    assert len(text) < len(json.dumps(full)) * 0.7


class _Platform(FakePlatform):
    async def get_run(self, run_id):
        return {
            "id": run_id,
            "user_id": "fixture",
            "status": "queued",
            "model": "m",
            "prompt": "hello",
            "input_files": [],
            "options": {"mode": "work"},
        }


def _call(index, arguments):
    return {"index": 0, "id": f"call_{index}", "type": "function", "function": {"name": "call_tool", "arguments": json.dumps(arguments)}}


def test_on_demand_tool_reads_its_description_then_runs_and_keeps_the_prefix():
    script = [
        {"tool_calls": [_call(1, {"name": "update_plan", "arguments": STEPS})]},  # guessed before reading: not run
        {"tool_calls": [_call(2, {"name": "update_plan", "arguments": STEPS})]},
        {"content": "done"},
    ]
    with scripted_server(script) as (url, requests):
        platform = _Platform()
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("fixture", attempt_id="attempt"))
    assert platform.statuses[-1][0] == "succeeded"
    assert len(requests) == 3
    for previous, current in pairwise(requests):
        assert_extension(previous, current)
    first, second = (json.loads(m["content"]) for m in requests[2]["messages"] if m["role"] == "tool")
    assert first["tool_doc"] == "update_plan" and "parameters" in first
    assert second == {"ok": True}
    # the history keeps what the model wrote; the run executed the real tool
    assert [c["function"]["name"] for m in requests[2]["messages"] for c in m.get("tool_calls") or []] == ["call_tool", "call_tool"]
    assert ("plan.updated", STEPS) in platform.events


def test_capability_search_finds_on_demand_tools():
    script = [
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "s",
                    "type": "function",
                    "function": {"name": "search_capabilities", "arguments": '{"query":"定时任务"}'},
                }
            ]
        },
        {"content": "done"},
    ]
    with scripted_server(script) as (url, requests):
        asyncio.run(AgentLoop(platform=_Platform(), broker=FakeBroker(), llm=ModelTransport(url)).process("fixture", attempt_id="attempt"))
    result = json.loads(next(m["content"] for m in requests[1]["messages"] if m["role"] == "tool"))
    assert {"kind": "tool", "name": "schedule", "description": deferred.SUMMARY["schedule"], "use": "call_tool"} in result["capabilities"]
