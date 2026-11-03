import asyncio
import copy
import json
from itertools import pairwise
from types import SimpleNamespace

import pytest

from ailab_platform.adapters.runtime import checkpoint_state, fresh_state
from ailab_platform.agent.discovery import REDUCED_V1, catalog_for_budget
from ailab_platform.agent.loop import AgentLoop, BudgetPause, _tools_for_run
from ailab_platform.agent.multimodal import image_part
from ailab_platform.agent.prompts import PLATFORM_PROMPT_VERSION, memory_prompt, system_prompt_for
from ailab_platform.agent.tools import tools_for_turn
from ailab_platform.catalog import Catalog, ModelSpec
from ailab_platform.contracts.context_summary import ContextSummary, canonical_summary
from ailab_platform.llm import LLMResponse
from ailab_platform.model_transport import ModelTransport
from tests.prefix_harness import assert_extension, scripted_server
from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM


def test_discovery_keeps_request_prefix_and_tools():
    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {
                "id": run_id,
                "user_id": "fixture",
                "status": "queued",
                "model": "m",
                "prompt": "hello",
                "input_files": [],
                "options": {"mode": "work", "capability_discovery": 1},
            }

    calls = [
        {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "search_capabilities", "arguments": '{"query":"pdf"}'},
        }
    ]
    with scripted_server([{"tool_calls": calls}, {"content": "done"}]) as (url, requests):
        platform = Platform()
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("fixture", attempt_id="attempt"))
    assert platform.statuses[-1][0] == "succeeded"
    assert len(requests) == 2
    assert_extension(*requests)
    assistant = requests[1]["messages"][len(requests[0]["messages"])]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"] == {
        "name": "search_capabilities",
        "arguments": json.dumps(json.loads(calls[0]["function"]["arguments"]), ensure_ascii=False),
    }


def test_clipped_tool_result_stays_identical_on_later_calls():
    class Broker(FakeBroker):
        async def get_file(self, lease_id, path):
            return b"x" * 100_000

    def tool_call(call_id, name, arguments):
        return {"tool_calls": [{"index": 0, "id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}]}

    responses = [
        tool_call("read", "read_file", '{"path":"/workspace/work/large.txt"}'),
        tool_call("plan", "update_plan", '{"steps":["finish"]}'),
        {"content": "done"},
    ]
    with scripted_server(responses) as (url, requests):
        platform = FakePlatform()
        asyncio.run(AgentLoop(platform=platform, broker=Broker(), llm=ModelTransport(url)).process("fixture", attempt_id="attempt"))
    assert platform.statuses[-1][0] == "succeeded"
    assert len(requests) == 3
    assert_extension(requests[0], requests[1])
    assert_extension(requests[1], requests[2])
    result = next(message for message in requests[1]["messages"] if message.get("tool_call_id") == "read")
    assert '"next_offset"' in result["content"] and len(result["content"]) < 100_000  # read_file returns a bounded window


def test_reasoning_is_echoed_without_rewriting_earlier_messages():
    calls = [{"index": 0, "id": "plan", "type": "function", "function": {"name": "update_plan", "arguments": '{"steps":["finish"]}'}}]
    with scripted_server([{"reasoning_content": "先检查", "tool_calls": calls}, {"content": "done"}]) as (url, requests):
        platform = FakePlatform()
        asyncio.run(
            AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url, echo_reasoning=True)).process(
                "fixture", attempt_id="attempt"
            )
        )
    assert platform.statuses[-1][0] == "succeeded"
    assert_extension(*requests)
    assert requests[1]["messages"][len(requests[0]["messages"])]["reasoning_content"] == "先检查"


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_next_run_extends_the_previous_run_wire_messages(mode):
    class Platform(FakePlatform):
        def __init__(self):
            super().__init__()
            self.checkpoints = {}

        async def get_checkpoint(self, run_id):
            return self.checkpoints.get(run_id)

        async def put_checkpoint(self, run_id, payload):
            step = self.checkpoints.get(run_id, {}).get("step", 0)
            assert payload["expected_step"] == step
            self.checkpoints[run_id] = {"step": step + 1, "state": copy.deepcopy(payload["state"])}
            return {"step": step + 1}

        async def get_run(self, run_id):
            run = {**await super().get_run(run_id), "prompt": "first" if run_id == "first" else "second", "options": {"mode": mode}}
            if run_id == "second":
                state = self.checkpoints["first"]["state"]
                run["previous_model_context"] = {
                    "messages": state["messages"],
                    "tools": state["tool_catalog_v1"],
                    "prompt_anchors": state["prompt_anchors"],
                }
            return run

    call = {"index": 0, "id": "plan", "type": "function", "function": {"name": "update_plan", "arguments": '{"steps":["finish"]}'}}
    with scripted_server(
        [{"tool_calls": [call]}, {"content": "first answer", "reasoning_content": "answer reasoning"}, {"content": "second answer"}]
    ) as (url, requests):
        platform = Platform()
        loop = AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url, echo_reasoning=True))
        asyncio.run(loop.process("first", attempt_id="a1"))
        asyncio.run(loop.process("second", attempt_id="a2"))
    assert [status for status, _ in platform.statuses if status == "succeeded"] == ["succeeded", "succeeded"]
    assert len(requests) == 3
    assert any(
        a in platform.checkpoints["second"]["state"]["prompt_anchors"] for a in platform.checkpoints["first"]["state"]["prompt_anchors"]
    )
    assert_extension(requests[1], requests[2])
    assert requests[2]["messages"] == [
        *requests[1]["messages"],
        {"role": "assistant", "content": "first answer", "reasoning_content": "answer reasoning"},
        {"role": "user", "content": "second"},
    ]


def test_parent_near_threshold_is_summarized_as_prefix_extension(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=6500, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    previous = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "旧" * 4850},
        {"role": "assistant", "content": "done"},
    ]
    current = {"role": "user", "content": "新" * 500}
    tools = [{"type": "function", "function": {"name": "example", "parameters": {"type": "object"}}}]
    with scripted_server([{"content": canonical_summary(ContextSummary(objective="summary"))}]) as (url, requests):
        platform = FakePlatform()
        llm = ModelTransport(url)
        run = {"context_settings": {"threshold_ratio": 0.8}, "_attempt_id": "attempt", "options": {"mode": "chat"}}
        loop = AgentLoop(platform=platform, llm=llm)
        result = asyncio.run(
            loop._compact_parent_context(
                "fixture",
                run,
                llm,
                "m",
                previous,
                current,
                tools,
                {"/workspace/outputs/source.txt": 4},
                {"/workspace/outputs/source.txt": "final.txt"},
            )
        )
    assert requests[0]["messages"][:-1] == previous
    assert requests[0]["tools"] == tools and requests[0]["tool_choice"] == "auto"
    assert result[:2] == previous[:2] and result[2]["content"].startswith("<conversation_checkpoint>\n") and result[-1] == current
    assert "final.txt" in result[3]["content"] and "/workspace/outputs/source.txt" in result[3]["content"]
    assert run["context_boundary"] == "compacted" and not platform.events


def test_parent_with_recall_compacts_with_instruction_to_forget_excerpts(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=6000, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    prior = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "旧" * 4850},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "recent"},
        {
            "role": "assistant",
            "content": "other message",
            "tool_calls": [{"id": "recall", "function": {"name": "recall_history", "arguments": "{}"}}],
        },
        {"role": "tool", "name": "recall_history", "tool_call_id": "recall", "content": "secret recall content"},
    ]
    current = {"role": "user", "content": "新" * 500}
    with scripted_server([{"content": canonical_summary(ContextSummary(objective="曾查询历史主题"))}]) as (url, requests):
        loop = AgentLoop(platform=FakePlatform(), llm=ModelTransport(url))
        result = asyncio.run(loop._compact_parent_context("r", {"_attempt_id": "a"}, loop.llm, "m", prior, current, []))
    assert "不要把 recall_history 的原文" in requests[0]["messages"][-1]["content"]
    assert any(m.get("content") == "other message" for m in result)
    recall = next(message for message in result if message.get("name") == "recall_history")
    assert json.loads(recall["content"]) == {"ok": False, "error": "历史已整理；需要详情请重新调用 recall_history"}
    assert "secret recall content" not in json.dumps(result, ensure_ascii=False)


def test_run_compaction_extends_wire_prefix_and_retains_complete_groups(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=8000, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "旧" * 5300},
        {"role": "assistant", "content": "旧" * 1200},
        {
            "role": "assistant",
            "content": "recent one",
            "tool_calls": [{"id": "x", "function": {"name": "recall_history", "arguments": "{}"}}],
        },
        {"role": "tool", "name": "recall_history", "tool_call_id": "x", "content": "secret recall content"},
        {"role": "assistant", "content": "recent two"},
    ]
    original = copy.deepcopy(messages)
    tools = [{"type": "function", "function": {"name": "example", "parameters": {"type": "object"}}}]
    with scripted_server([{"content": canonical_summary(ContextSummary(objective="summary"))}]) as (url, requests):
        platform = FakePlatform()
        loop = AgentLoop(platform=platform, llm=ModelTransport(url))
        loop._states["r"] = {
            **fresh_state(),
            "messages": messages,
            "user_message_index": 2,
            "delivered": {"/workspace/outputs/report.txt": 12},
        }
        loop._steps["r"] = 0

        async def checkpoint(*args):
            pass

        monkeypatch.setattr(loop, "_checkpoint", checkpoint)
        turn = SimpleNamespace(
            messages=messages,
            run={"context_settings": {"threshold_ratio": 0.8}, "_attempt_id": "a", "_ledger_tokens": 0, "prompt": "constraints"},
        )
        try:
            asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", tools))
        except BudgetPause:
            pass  # Still examine the request made before a non-converging compaction paused.
    assert requests[0]["messages"][:-1] == original
    assert requests[0]["tools"] == tools and requests[0]["tool_choice"] == "auto"
    assert messages[:3] == original[:3]
    assert messages[3]["content"].startswith("<conversation_checkpoint>\n")
    assert "report.txt" in messages[4]["content"] and "/workspace/outputs/report.txt" in messages[4]["content"]
    assert messages[5]["content"].startswith("<loaded_modules>")
    assert "secret recall content" not in json.dumps(messages, ensure_ascii=False)
    assert loop._states["r"]["user_message_index"] == 2
    assert loop._states["r"]["context_boundary"]["reason"] == "compacted"


def test_unchanged_memory_refresh_preserves_all_request_messages():
    memory = {"enabled": True, "entries": [{"id": "m1", "kind": "user", "content": "中文"}]}

    class Platform(FakePlatform):
        async def get_memory_context(self, run_id):
            return memory

    async def exercise():
        platform = Platform()
        messages = [{"role": "system", "content": "rules"}, {"role": "system", "content": memory_prompt(memory)}]
        original = copy.deepcopy(messages)
        loop = AgentLoop(platform=platform)
        for step in range(5):
            await loop._model_attempt(ScriptedLLM([LLMResponse(content="ok")]), "r1", {"_attempt_id": "a"}, "m", messages, step, tools=[])
            assert messages == original

    asyncio.run(exercise())


def test_memory_deletion_appends_note_without_rewriting_prior_results():
    initial = {"revision": 1, "enabled": True, "entries": [{"id": "m1", "kind": "user", "content": "旧偏好"}]}
    latest = {"revision": 2, "enabled": True, "entries": []}

    class Platform(FakePlatform):
        async def get_memory_context(self, run_id):
            return latest

        async def memory(self, run_id, request):
            return latest

    async def exercise():
        loop = AgentLoop(platform=Platform())
        loop._states["r"] = {"memory_observed": copy.deepcopy(initial)}
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "system", "content": memory_prompt(initial)},
            {"role": "assistant", "tool_calls": [{"id": "one", "function": {"name": "memory", "arguments": '{"action":"list"}'}}]},
            {"role": "tool", "name": "memory", "tool_call_id": "one", "content": "原始结果 旧偏好"},
        ]
        original = copy.deepcopy(messages)
        await loop._model_attempt(ScriptedLLM([LLMResponse(content="ok")]), "r", {"_attempt_id": "a"}, "m", messages, 0, tools=[])
        assert messages[: len(original)] == original
        assert "记忆 m1 已删除，不得再使用" in messages[-1]["content"]
        assert "旧偏好" not in messages[-1]["content"]
        count = len(messages)
        await loop._model_attempt(ScriptedLLM([LLMResponse(content="ok")]), "r", {"_attempt_id": "a"}, "m", messages, 1, tools=[])
        assert len(messages) == count

    asyncio.run(exercise())


def test_recall_revalidation_preserves_result_and_tombstones_revoked_source():
    from ailab_platform.clients import ClientError

    class Platform(FakePlatform):
        revoked = False
        calls = []

        async def get_memory_context(self, run_id):
            return {}

        async def recall(self, run_id, request):
            self.calls.append(request["run_id"])
            if self.revoked:
                raise ClientError("POST", "/recall", 404, "gone")
            return {"run_id": request["run_id"]}

    async def exercise():
        platform = Platform()
        loop = AgentLoop(platform=platform)
        messages = [
            {"role": "tool", "name": "recall_history", "content": '{"results":[{"run_id":"source","text":"old"},{"run_id":"source2"}]}'}
        ]
        original = copy.deepcopy(messages)
        await loop._model_attempt(ScriptedLLM([LLMResponse(content="ok")]), "r", {"_attempt_id": "a"}, "m", messages, 0, tools=[])
        assert messages == original
        platform.revoked = True
        await loop._model_attempt(ScriptedLLM([LLMResponse(content="ok")]), "r", {"_attempt_id": "a"}, "m", messages, 1, tools=[])
        assert messages == original
        assert platform.calls == ["source", "source2"]
        loop._states["next_run"] = {**fresh_state(), "messages": messages}
        await loop._model_attempt(ScriptedLLM([LLMResponse(content="ok")]), "next_run", {"_attempt_id": "a"}, "m", messages, 0, tools=[])
        assert json.loads(messages[0]["content"]) == {"ok": False, "error": "引用的历史已不可用"}
        assert any(kind == "agent.context_boundary" and payload["reason"] == "access_revoked" for kind, payload in platform.events)

    asyncio.run(exercise())


def test_memory_entry_omitted_by_turn_relevance_is_not_reported_deleted():
    entry = {"id": "m1", "kind": "memory", "content": "项目事实"}

    class Platform(FakePlatform):
        async def get_memory_context(self, run_id):
            return {"revision": 2, "entries": []}

        async def memory(self, run_id, request):
            return {"revision": 2, "entries": [entry]}

    async def exercise():
        loop = AgentLoop(platform=Platform())
        loop._states["r"] = {"memory_observed": {"revision": 1, "entries": [entry]}}
        messages = [{"role": "system", "content": "rules"}]
        await loop._model_attempt(ScriptedLLM([LLMResponse(content="ok")]), "r", {"_attempt_id": "a"}, "m", messages, 0, tools=[])
        assert len(messages) == 1

    asyncio.run(exercise())


def test_reduced_catalog_is_fixed_and_selected_once_from_model_budget():
    from ailab_platform.agent.discovery import SEARCH
    from ailab_platform.model_policy import estimator

    catalog = [SEARCH, *tools_for_turn()]
    reduced = [tool for tool in catalog if tool["function"]["name"] in REDUCED_V1]
    assert catalog_for_budget(catalog, estimator.raw(catalog)) == catalog
    assert catalog_for_budget(catalog, estimator.raw(reduced)) == reduced
    assert _tools_for_run({"_fixed_catalog": reduced}) == reduced


def test_reported_cache_tokens_identify_main_title_and_compaction(monkeypatch):
    observed = []
    monkeypatch.setattr(
        "ailab_platform.agent.loop.metrics.record_prompt_cache", lambda mode, kind, usage: observed.append((mode, kind, usage))
    )

    async def exercise():
        loop = AgentLoop(platform=FakePlatform())
        for step in (0, -2, -3, -1, 5):
            usage = {"prompt_tokens": 100, "cached_tokens": 80}
            await loop._model_attempt(
                ScriptedLLM([LLMResponse(content="ok", usage=usage)]),
                "r1",
                {"_attempt_id": "a", "options": {"mode": "chat"}},
                "m",
                [{"role": "user", "content": "hello"}],
                step,
                tools=[],
            )

    asyncio.run(exercise())
    assert [(mode, kind) for mode, kind, _ in observed] == [
        ("chat", "main"),
        ("chat", "title"),
        ("chat", "compaction"),
        ("chat", "compaction"),
        ("chat", "main"),
    ]
    assert all(usage["cached_tokens"] == 80 and usage["prompt_tokens"] == 100 for _, _, usage in observed)


@pytest.mark.parametrize("data,unchanged", [(b"same", True), (b"changed", False)])
def test_checkpoint_image_restore_requires_identical_authorized_bytes(monkeypatch, data, unchanged):
    class Platform(FakePlatform):
        async def get_file_image(self, file_id, run_id):
            return data

    monkeypatch.setattr(
        "ailab_platform.agent.loop.get_catalog",
        lambda: Catalog([ModelSpec("m", "m", provider="mock", capabilities=frozenset({"vision"}))]),
    )
    file = {"id": "image", "filename": "photo.webp", "content_type": "image/webp"}
    original = image_part(b"same", "image/webp")
    state = fresh_state()
    state["messages"] = [{"role": "user", "content": [original]}]
    saved = checkpoint_state(state, {original["image_url"]["url"]: file})
    loop = AgentLoop(platform=Platform())
    if unchanged:
        asyncio.run(loop._restore_images("r1", {"model": "m"}, saved))
        assert saved["messages"][0]["content"] == [original]
    else:
        with pytest.raises(BudgetPause, match="图片内容已变化"):
            asyncio.run(loop._restore_images("r1", {"model": "m"}, saved))


def test_legacy_image_checkpoint_trusts_first_authorized_read_and_then_verifies(monkeypatch):
    class Platform(FakePlatform):
        data = b"first authorized image"

        async def get_file_image(self, file_id, run_id):
            return self.data

    monkeypatch.setattr(
        "ailab_platform.agent.loop.get_catalog",
        lambda: Catalog([ModelSpec("m", "m", provider="mock", capabilities=frozenset({"vision"}))]),
    )
    file = {"id": "image", "filename": "photo.webp", "content_type": "image/webp"}
    state = fresh_state()
    state["messages"] = [{"role": "user", "content": [{"type": "ailab_image", "file": file}]}]
    platform = Platform()
    loop = AgentLoop(platform=platform)
    run = {"model": "m"}
    assert asyncio.run(loop._restore_images("r1", run, state)) is True
    saved = checkpoint_state(state, run["_image_files"])
    assert saved["messages"][0]["content"][0]["sha256"]
    platform.data = b"different image"
    with pytest.raises(BudgetPause, match="图片内容已变化"):
        asyncio.run(loop._restore_images("r1", {"model": "m"}, saved))


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_platform_system_prompt_does_not_include_user_skill_state(mode):
    first = system_prompt_for(mode, "private-one", frozenset({"disabled-one"}), [{"name": "private-one"}], "secret one")
    second = system_prompt_for(mode, "private-two", frozenset({"disabled-two"}), [{"name": "private-two"}], "secret two")
    assert first == second
    assert PLATFORM_PROMPT_VERSION in first
    assert all(value not in first for value in ("private-one", "disabled-one", "secret one", "private-two", "disabled-two", "secret two"))


def test_revoked_parent_rebuilds_valid_history_without_replaying_tools(monkeypatch):
    from ailab_platform.clients import ClientError

    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([ModelSpec("m", "m", provider="mock")]))

    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {
                **await super().get_run(run_id),
                "previous_model_context": {
                    "messages": [
                        {"role": "system", "content": "old"},
                        {
                            "role": "assistant",
                            "tool_calls": [{"id": "exec", "type": "function", "function": {"name": "exec", "arguments": "{}"}}],
                        },
                        {"role": "tool", "name": "exec", "tool_call_id": "exec", "content": "already executed"},
                        {
                            "role": "assistant",
                            "tool_calls": [{"id": "delivery", "type": "function", "function": {"name": "deliver_file", "arguments": "{}"}}],
                        },
                        {"role": "tool", "name": "deliver_file", "tool_call_id": "delivery", "content": "already delivered"},
                        {"role": "tool", "name": "recall_history", "content": '{"results":[{"run_id":"gone","text":"secret"}]}'},
                        {"role": "assistant", "content": "secret from source"},
                    ],
                    "tools": [],
                },
            }

        async def recall(self, run_id, payload):
            raise ClientError("POST", "/recall", 404, "gone")

        async def get_model_context(self, run_id, after=None):
            return {
                "summary": "",
                "through_run_id": None,
                "history": [{"id": "old", "prompt": "valid", "reply": "done"}],
                "has_more": False,
            }

    with scripted_server([{"content": "done"}]) as (url, requests):
        platform, broker = Platform(), FakeBroker()
        asyncio.run(AgentLoop(platform=platform, broker=broker, llm=ModelTransport(url)).process("r", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert any(kind == "agent.context_boundary" and payload["reason"] == "access_revoked" for kind, payload in platform.events)
    assert "secret" not in json.dumps(requests[0], ensure_ascii=False)
    assert any(m.get("content") == "valid" for m in requests[0]["messages"])
    assert broker.leases == 0 and not broker.put_calls


def test_rebuilt_history_summary_appends_instruction_to_packed_history(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=6500, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))

    class Platform(FakePlatform):
        async def get_model_context(self, run_id, after=None):
            return {
                "summary": "",
                "through_run_id": None,
                "history": [{"id": "old", "prompt": "旧" * 5300, "reply": "done"}],
                "has_more": False,
            }

        async def save_model_summary(self, run_id, payload):
            self.saved = payload

    tools = [{"type": "function", "function": {"name": "example", "parameters": {"type": "object"}}}]
    base = [{"role": "system", "content": "rules"}, {"role": "system", "content": "context"}, {"role": "user", "content": "current"}]
    with scripted_server([{"content": canonical_summary(ContextSummary(objective="summary"))}]) as (url, requests):
        platform = Platform()
        loop = AgentLoop(platform=platform, llm=ModelTransport(url))
        result = asyncio.run(
            loop._assemble_context(
                "r", {"context_settings": {"threshold_ratio": 0.8}, "_attempt_id": "a", "_fixed_catalog": tools}, "m", base
            )
        )
    assert requests[0]["messages"][:-1] == [*base[:2], {"role": "user", "content": "旧" * 5300}, {"role": "assistant", "content": "done"}]
    assert requests[0]["tools"] == tools and requests[0]["tool_choice"] == "auto"
    assert result[:2] == base[:2] and result[-1] == base[-1] and result[2]["content"].startswith("<conversation_checkpoint>\n")
    assert platform.saved["through_run_id"] == "old"


@pytest.mark.parametrize("split", [False, True])
def test_legacy_checkpoint_rebuilds_once_and_preserves_tool_fences(split):
    state = fresh_state()
    state["messages"] = (
        [{"role": "system", "content": "legacy rules"}, {"role": "system", "content": "<ailab_memory>old</ailab_memory>"}]
        if split
        else [{"role": "system", "content": "legacy rules\n<ailab_memory>old</ailab_memory>"}]
    ) + [{"role": "user", "content": "current task"}]
    state["user_message_index"] = len(state["messages"]) - 1
    state["delivered"] = {"/workspace/out.txt": 12}
    state["tool_log"] = {"0:0": {"name": "exec", "status": "started"}}
    platform, broker = FakePlatform(), FakeBroker()
    platform.checkpoint = {"step": 1, "state": state}
    with scripted_server([{"content": "done"}]) as (url, requests):
        asyncio.run(AgentLoop(platform=platform, broker=broker, llm=ModelTransport(url)).process("r", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert [event for event in platform.events if event == ("agent.context_boundary", {"reason": "rebuilt"})] == [
        ("agent.context_boundary", {"reason": "rebuilt"})
    ]
    assert "legacy rules" not in json.dumps(requests[0], ensure_ascii=False)
    assert requests[0]["messages"][1]["role"] == "system"
    assert requests[0]["messages"][2] == {"role": "user", "content": "current task"}
    assert platform.checkpoint["state"]["delivered"] == {"/workspace/out.txt": 12}
    assert "0:0" in platform.checkpoint["state"]["tool_log"] and broker.leases == 0


def _plan_call(number):
    return {
        "tool_calls": [
            {
                "index": 0,
                "id": f"plan{number}",
                "type": "function",
                "function": {"name": "update_plan", "arguments": '{"steps":["finish"]}'},
            }
        ]
    }


@pytest.mark.parametrize("strategy", ["reference", "inline"])
def test_attachment_and_picked_skill_keep_request_prefix(strategy):
    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {
                **await super().get_run(run_id),
                "input_files": [
                    {"file_id": "file1", "filename": "note.txt", "version": 1, "text": "attachment body", "input_strategy": strategy}
                ],
                "options": {"mode": "work", "skill": "pptx"},
            }

    with scripted_server([_plan_call(1), {"content": "done"}]) as (url, requests):
        platform = Platform()
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert_extension(*requests)
    original = requests[0]["messages"]
    assert any("file1" in str(message.get("content")) for message in original)
    assert ("attachment body" in json.dumps(original, ensure_ascii=False)) == (strategy == "inline")
    assert any(message.get("role") == "tool" and "演示设计" in message.get("content", "") for message in original)


def test_failed_model_request_retry_reuses_identical_body(monkeypatch):
    from ailab_platform.config import settings

    monkeypatch.setattr(settings, "llm_max_retries", 1)
    monkeypatch.setattr(settings, "llm_retry_backoff_seconds", 0)
    with scripted_server([{"status": 503}, _plan_call(1), {"content": "done"}]) as (url, requests):
        platform = FakePlatform()
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert requests[0] == requests[1]
    assert_extension(requests[1], requests[2])


@pytest.mark.parametrize("reasoning", ["wrap-up reasoning", ""])
def test_checkpoint_continue_keeps_previous_request_prefix(monkeypatch, reasoning):
    from ailab_platform.config import settings

    class Platform(FakePlatform):
        window = "first"

        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "budget_window_id": self.window}

    platform = Platform()
    with scripted_server([_plan_call(1), {"content": "paused summary", "reasoning_content": reasoning}, {"content": "done"}]) as (
        url,
        requests,
    ):
        monkeypatch.setattr(settings, "max_agent_steps", 1)
        loop = AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url, echo_reasoning=True))
        asyncio.run(loop.process("r", attempt_id="first"))
        assert platform.paused is not None
        platform.window = "second"
        monkeypatch.setattr(settings, "max_agent_steps", 4)
        asyncio.run(loop.process("r", attempt=2, attempt_id="second"))
    assert platform.statuses[-1][0] == "succeeded"
    assert_extension(requests[0], requests[1])
    assert requests[1]["tool_choice"] == "auto" and "thinking" not in requests[1]  # same shape as the run's calls
    assert_extension(requests[1], requests[2])
    summary = next(message for message in requests[2]["messages"] if message.get("content") == "paused summary")
    assert summary["reasoning_content"] == reasoning
    assert any("此前收尾限制已解除" in str(m.get("content")) for m in requests[2]["messages"])


def test_steer_wrap_up_and_stuck_notes_are_appended(monkeypatch):
    from ailab_platform.agent.loop import STUCK_NOTE, WRAP_UP_NOTE
    from ailab_platform.config import settings

    monkeypatch.setattr(settings, "max_agent_steps", 7)
    count = 0

    def detect(*args):
        nonlocal count
        count += 1
        return "repeated" if count == 2 else None

    monkeypatch.setattr("ailab_platform.agent.loop.detect_loop", detect)

    class Platform(FakePlatform):
        async def claim_steers(self, run_id):
            return {"items": [{"id": "steer1", "prompt": "additional request"}]}

    class Loop(AgentLoop):
        async def _apply_steers(self, run_id, turn, *, pending=None):
            await super()._apply_steers(run_id, turn, pending=self._states[run_id]["next_step"] == 1)

    with scripted_server([*(_plan_call(i) for i in range(4)), {"content": "done"}]) as (url, requests):
        platform = Platform()
        asyncio.run(Loop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    for before, after in pairwise(requests):
        assert_extension(before, after)
    assert any(m.get("content") == "additional request" for m in requests[1]["messages"])
    assert any(m.get("content") == STUCK_NOTE for m in requests[1]["messages"])
    assert any(m.get("content") == WRAP_UP_NOTE.format(n=3) for m in requests[-1]["messages"][len(requests[-2]["messages"]) :])
    assert any(kind == "agent.budget" and payload.get("kind") == "nudge" for kind, payload in platform.events)


def test_memory_snapshot_is_replaced_at_compaction_boundary(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=32000, max_output_tokens=8192, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    old = {"revision": 1, "entries": [{"id": "old", "content": "旧专属事实"}]}
    new = {"revision": 2, "entries": [{"id": "new", "content": "最新"}]}
    prior = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "project" + memory_prompt(old)},
        {"role": "user", "content": "旧" * 26000},
        {"role": "assistant", "content": "done"},
    ]

    class Platform(FakePlatform):
        async def get_memory_context(self, run_id):
            return new

    with scripted_server([{"content": canonical_summary(ContextSummary(objective="summary"))}]) as (url, requests):
        loop = AgentLoop(platform=Platform(), llm=ModelTransport(url))
        result = asyncio.run(
            loop._compact_parent_context(
                "r",
                {"context_settings": {"threshold_ratio": 0.8}, "_attempt_id": "a"},
                loop.llm,
                "m",
                prior,
                {"role": "user", "content": "新" * 500},
                [],
            )
        )
    assert requests[0]["messages"][:-1] == prior
    assert "旧专属事实" not in result[1]["content"] and "最新" in result[1]["content"]
    assert loop._states["r"]["memory_observed"]["revision"] == 2


def test_compaction_drops_old_image_and_preserves_recent_image(monkeypatch):
    from ailab_platform.config import settings

    monkeypatch.setattr(settings, "vision_input_tokens", 16)
    spec = ModelSpec("m", "m", provider="mock", context_tokens=6500, max_output_tokens=512, capabilities=frozenset({"tools", "vision"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    old_image, recent_image = image_part(b"old image", "image/png"), image_part(b"recent image", "image/png")
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": [{"type": "text", "text": "旧" * 5300}, old_image]},
        {"role": "user", "content": "current task"},
        {"role": "assistant", "content": "old"},
        {
            "role": "assistant",
            "content": "recent one",
            "tool_calls": [{"id": "x", "function": {"name": "recall_history", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "x", "content": "result"},
        {"role": "assistant", "content": "recent two"},
        {"role": "user", "content": [recent_image]},
    ]
    original = copy.deepcopy(messages)
    with scripted_server([{"content": canonical_summary(ContextSummary(objective="summary"))}]) as (url, requests):
        loop = AgentLoop(platform=FakePlatform(), llm=ModelTransport(url))
        loop._states["r"] = {**fresh_state(), "messages": messages, "user_message_index": 3, "tool_image_indices": [8]}

        async def checkpoint(*args):
            pass

        monkeypatch.setattr(loop, "_checkpoint", checkpoint)
        asyncio.run(
            loop._compact_work_context(
                "r",
                SimpleNamespace(messages=messages, run={"context_settings": {"threshold_ratio": 0.8}, "_attempt_id": "a"}),
                loop.llm,
                "m",
                [],
            )
        )
    assert requests[0]["messages"][:-1] == original
    assert messages[loop._states["r"]["user_message_index"]] == {"role": "user", "content": "current task"}
    assert messages[-1]["content"] == [recent_image]
    assert old_image not in [part for m in messages if isinstance(m.get("content"), list) for part in m["content"]]
    assert messages[-1]["content"] == [recent_image]


def test_rebuilt_summary_does_not_fallback_to_a_different_model(monkeypatch):
    from ailab_platform.config import settings
    from ailab_platform.llm import LLMError

    monkeypatch.setattr(settings, "llm_max_retries", 0)
    first = ModelSpec("m", "m", provider="mock", context_tokens=6500, max_output_tokens=512, fallback=["backup"])
    second = ModelSpec("backup", "backup", provider="mock", context_tokens=6500, max_output_tokens=512)
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([first, second]))

    class Platform(FakePlatform):
        async def get_model_context(self, run_id, after=None):
            return {
                "summary": "",
                "through_run_id": None,
                "history": [{"id": "old", "prompt": "旧" * 5300, "reply": "done"}],
                "has_more": False,
            }

    class Failure(ScriptedLLM):
        async def chat(self, messages, tools, model, user_id=None):
            raise LLMError("unavailable", retryable=True)

    fallback = []
    monkeypatch.setattr("ailab_platform.agent.loop.llm_for", lambda model: fallback.append(model))
    platform = Platform()
    loop = AgentLoop(platform=platform, llm=Failure([]))
    with pytest.raises(BudgetPause, match="上下文整理失败"):
        asyncio.run(
            loop._assemble_context(
                "r",
                {"context_settings": {"threshold_ratio": 0.8}, "_attempt_id": "a", "_fixed_catalog": []},
                "m",
                [{"role": "system", "content": "rules"}, {"role": "user", "content": "current"}],
            )
        )
    assert not fallback and not any(kind == "agent.model_fallback" for kind, _ in platform.events)


@pytest.mark.parametrize("scope", ["parent", "run"])
def test_compaction_retries_transient_failure_on_same_model(monkeypatch, scope):
    from ailab_platform.config import settings
    from ailab_platform.llm import LLMError

    spec = ModelSpec("m", "m", provider="mock", context_tokens=6500, max_output_tokens=512)
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    monkeypatch.setattr(settings, "llm_max_retries", 1)
    monkeypatch.setattr(settings, "llm_retry_backoff_seconds", 0)
    loop = AgentLoop(platform=FakePlatform(), llm=ScriptedLLM([]))
    calls = []

    async def attempt(llm, run_id, run, model, messages, step, tools, **kwargs):
        calls.append((model, step, kwargs["tool_choice"]))
        if len(calls) == 1:
            raise LLMError("temporary", retryable=True)
        return LLMResponse(content=canonical_summary(ContextSummary(objective="summary")))

    async def checkpoint(*args):
        pass

    monkeypatch.setattr(loop, "_model_attempt", attempt)
    monkeypatch.setattr(loop, "_checkpoint", checkpoint)
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "旧" * 5300},
        {"role": "assistant", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "assistant", "content": "c"},
    ]
    run = {"context_settings": {"threshold_ratio": 0.8}, "_attempt_id": "a"}
    if scope == "parent":
        asyncio.run(loop._compact_parent_context("r", run, loop.llm, "m", messages, {"role": "user", "content": "新" * 500}, []))
    else:
        messages[2]["content"] = "旧" * 5500
        loop._states["r"] = {"user_message_index": 2}
        with pytest.raises(BudgetPause):
            asyncio.run(loop._compact_work_context("r", SimpleNamespace(messages=messages, run=run), loop.llm, "m", []))
    assert calls == [("m", -3, None), ("m", -3, None)]


def test_empty_context_layout_survives_three_resumes():
    platform = FakePlatform()
    loop = AgentLoop(platform=platform, llm=ScriptedLLM([]))
    base = [{"role": "system", "content": "rules"}, {"role": "user", "content": "task"}]
    messages = asyncio.run(loop._assemble_context("r", {}, "m", base))
    assert messages[1] == {"role": "system", "content": ""}
    state = fresh_state()
    state["messages"] = messages
    state["user_message_index"] = 2
    platform.checkpoint = {"step": 1, "state": state}
    for _ in range(3):
        before = copy.deepcopy(platform.checkpoint["state"]["messages"])
        asyncio.run(AgentLoop(platform=platform, llm=ScriptedLLM([LLMResponse(content="done")])).process("r", attempt_id="a"))
        assert platform.checkpoint["state"]["messages"][: len(before)] == before
        assert not any(event == ("agent.context_boundary", {"reason": "rebuilt"}) for event in platform.events)


def test_delivery_note_preserves_delivered_filename():
    note = AgentLoop._delivery_note({"/workspace/outputs/source.txt": 4}, {"/workspace/outputs/source.txt": "final.txt"})
    assert "final.txt：/workspace/outputs/source.txt" in note["content"]


def test_summary_keeps_request_shape_and_appends_paired_tool_refusals(monkeypatch):
    """tool_choice=none or a reasoning toggle re-renders the provider prompt and loses the cached prefix."""
    spec = ModelSpec("m", "m", provider="mock", context_tokens=6000, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    previous = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "旧" * 4850},
        {"role": "assistant", "content": "a"},
    ]
    tools = [{"type": "function", "function": {"name": "noop", "parameters": {"type": "object"}}}]
    call = {"tool_calls": [{"index": 0, "id": "c1", "type": "function", "function": {"name": "noop", "arguments": "{}"}}]}
    with scripted_server([call, {"content": canonical_summary(ContextSummary(objective="summary"))}]) as (url, requests):
        transport = ModelTransport(url, disable_reasoning_for_quiet=True)
        loop = AgentLoop(platform=FakePlatform(), llm=transport)
        result = asyncio.run(
            loop._compact_parent_context(
                "r", {"_attempt_id": "a"}, transport, "m", previous, {"role": "user", "content": "新" * 500}, tools
            )
        )
    assert requests[0]["tool_choice"] == "auto" and "thinking" not in requests[0]
    assert requests[1]["tool_choice"] == "auto" and "thinking" not in requests[1]
    assert_extension(requests[0], requests[1])
    refused = next(m for m in requests[1]["messages"] if m.get("tool_call_id") == "c1")
    assert not json.loads(refused["content"])["ok"]
    assert result[2]["content"].startswith("<conversation_checkpoint>\n")


def test_office_skill_loaded_earlier_in_the_conversation_is_not_sent_again():
    # Work prompts naming a deck load platform.pptx; repeating that 3k-token result every turn wasted it each time.
    class Platform(FakePlatform):
        def __init__(self):
            super().__init__()
            self.checkpoints = {}

        async def get_checkpoint(self, run_id):
            return self.checkpoints.get(run_id)

        async def put_checkpoint(self, run_id, payload):
            step = self.checkpoints.get(run_id, {}).get("step", 0)
            self.checkpoints[run_id] = {"step": step + 1, "state": copy.deepcopy(payload["state"])}
            return {"step": step + 1}

        async def get_run(self, run_id):
            run = {**await super().get_run(run_id), "prompt": f"{run_id} 演示文稿", "options": {"mode": "work"}}
            if run_id == "second":
                state = self.checkpoints["first"]["state"]
                run["previous_model_context"] = {
                    "messages": state["messages"],
                    "tools": state["tool_catalog_v1"],
                    "prompt_anchors": state["prompt_anchors"],
                }
            return run

    def skill_loads(request):
        return [m for m in request["messages"] if m.get("role") == "tool" and m.get("name") == "load_skill"]

    with scripted_server([{"content": "first answer"}, {"content": "second answer"}]) as (url, requests):
        platform = Platform()
        loop = AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url))
        asyncio.run(loop.process("first", attempt_id="a1"))
        asyncio.run(loop.process("second", attempt_id="a2"))
    assert [status for status, _ in platform.statuses if status == "succeeded"] == ["succeeded", "succeeded"]
    assert len(skill_loads(requests[0])) == len(skill_loads(requests[1])) == 1
    assert_extension(requests[0], requests[1])


def test_carried_skill_must_match_version_and_mode():
    from ailab_platform.agent.loop import _carried_skills
    from ailab_platform.agent.tools import tool_result_message

    call = {"id": "c1", "type": "function", "function": {"name": "load_skill", "arguments": '{"name": "platform.pptx"}'}}
    loaded = {"ok": True, "skill": "text", "version": "v1", "mode": "work"}
    messages = [{"role": "assistant", "content": "", "tool_calls": [call]}, tool_result_message("c1", "load_skill", loaded)]
    assert _carried_skills(messages, {"platform.pptx": "v1", "platform.docx": "v1"}, "work") == {"platform.pptx"}
    assert not _carried_skills(messages, {"platform.pptx": "v2"}, "work")
    assert not _carried_skills(messages, {"platform.pptx": "v1"}, "chat")
    failed = [messages[0], tool_result_message("c1", "load_skill", {"ok": False, "error": "x"})]
    assert not _carried_skills(failed, {"platform.pptx": "v1"}, "work")


def test_memory_editor_receives_displayed_snapshot_without_adding_it_to_chat(monkeypatch):
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from ailab_platform.api.main import app
    from ailab_platform.api.routes_memory import memory_for
    from ailab_platform.db import SessionLocal
    from ailab_platform.memory_dream import service
    from ailab_platform.memory_dream.prompts import EDIT_SYSTEM
    from ailab_platform.memory_dream.summary import digest
    from ailab_platform.models import MemorySummary, User
    from tests.test_memory_hardening import put
    from tests.test_memory_recall import _uid
    from tests.test_skill_settings import _user

    requests = []

    async def chat(messages, model, user):
        requests.append(copy.deepcopy(messages))
        return json.dumps({"ops": [], "reply": "请明确要修改的内容。"}), {}

    monkeypatch.setattr(service, "_chat", chat)
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        doc = put(c, me, [{"content": "喜欢中文回答"}])
        snapshot = "### 偏好\n- 中文回答。\n### 工作\n- 摘要中的旧信息。"
        with SessionLocal() as db:
            db.add(MemorySummary(user_id=uid, text=snapshot, source_digest=digest(doc["entries"]), generated_at=datetime.now(UTC)))
            db.commit()
        wrong = c.post("/v1/memory/summary/edit", headers=me, json={"instruction": "删除第二段", "summary_text": "另一个摘要"})
        assert wrong.status_code == 409 and requests == []
        response = c.post("/v1/memory/summary/edit", headers=me, json={"instruction": "删除第二段", "summary_text": snapshot})
        assert response.status_code == 202
        assert len(requests) == 1
        assert requests[0][0] == {"role": "system", "content": EDIT_SYSTEM}
        payload = json.loads(requests[0][1]["content"])
        assert payload["summary"] == snapshot and payload["instruction"] == "删除第二段"
        assert payload["entries"] == [{k: doc["entries"][0][k] for k in ("id", "kind", "content")}]
        with SessionLocal() as db:
            context = memory_prompt(memory_for(db.get(User, uid), for_model=True))
        assert "摘要中的旧信息" not in context and snapshot not in context


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_ui_docs_deduplicate_across_calls_and_runs_without_rewriting_prefix(monkeypatch, mode):
    from ailab_platform.config import settings
    from ailab_platform.contracts.genui import docs_for, guide_for

    monkeypatch.setattr(settings, "genui_enabled", True)
    monkeypatch.setattr(settings, "genui_html_enabled", False)
    spec = ModelSpec("m", "m", provider="mock", context_tokens=65536, max_output_tokens=512, capabilities=frozenset({"tools", "genui"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))

    class Platform(FakePlatform):
        def __init__(self):
            super().__init__()
            self.checkpoints = {}

        async def get_checkpoint(self, run_id):
            return self.checkpoints.get(run_id)

        async def put_checkpoint(self, run_id, payload):
            step = self.checkpoints.get(run_id, {}).get("step", 0)
            assert payload["expected_step"] == step
            self.checkpoints[run_id] = {"step": step + 1, "state": copy.deepcopy(payload["state"])}
            return {"step": step + 1}

        async def get_run(self, run_id):
            run = {**await super().get_run(run_id), "options": {"mode": mode}, "prompt": run_id}
            if run_id == "second":
                state = self.checkpoints["first"]["state"]
                run["previous_model_context"] = {
                    "messages": state["messages"],
                    "tools": state["tool_catalog_v1"],
                    "prompt_anchors": state["prompt_anchors"],
                }
            return run

    def call(identity, names):
        return {
            "tool_calls": [
                {
                    "index": 0,
                    "id": identity,
                    "type": "function",
                    "function": {"name": "ui_components", "arguments": json.dumps({"names": names})},
                }
            ]
        }

    responses = [
        call("first-docs", ["LineChart"]),
        call("repeat", ["LineChart"]),
        call("new-docs", ["BarChart"]),
        {"content": "完成"},
        call("next-run", ["LineChart", "BarChart"]),
        {"content": "已修改"},
    ]
    with scripted_server(responses) as (url, requests):
        platform = Platform()
        broker = FakeBroker()
        loop = AgentLoop(platform=platform, broker=broker, llm=ModelTransport(url))
        asyncio.run(loop.process("first", attempt_id="a"))
        asyncio.run(loop.process("second", attempt_id="b"))
    assert len(requests) == 6
    for previous, current in pairwise(requests):
        assert_extension(previous, current)
    results = {
        message["tool_call_id"]: json.loads(message["content"])
        for message in requests[-1]["messages"]
        if message.get("name") == "ui_components" and message.get("role") == "tool"
    }
    assert results["first-docs"]["guide"] == guide_for(False)
    assert results["first-docs"]["components"] == docs_for(["LineChart"], False)[0]
    assert results["repeat"]["components"] == "" and "guide" not in results["repeat"]
    assert results["new-docs"]["already_available"] == ["Series"]
    assert "BarChart(" in results["new-docs"]["components"]
    assert docs_for(["Series"], False)[0] not in results["new-docs"]["components"]
    assert results["next-run"]["components"] == "" and "guide" not in results["next-run"]
    assert broker.leases == 0


@pytest.mark.parametrize("retained", [False, True])
def test_ui_docs_after_real_compaction_follow_retained_messages(monkeypatch, retained):
    from ailab_platform.agent.tools import tool_result_message, ui_components

    spec = ModelSpec("m", "m", provider="mock", context_tokens=12000, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    docs = ui_components({"names": ["Text"]}, {}, html_enabled=False)
    pair = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "docs", "type": "function", "function": {"name": "ui_components", "arguments": '{"names":["Text"]}'}}],
        },
        tool_result_message("docs", "ui_components", docs),
    ]
    old = {"role": "assistant", "content": "旧" * 5000}
    recent = {"role": "assistant", "content": "近期回答"}
    body = [old, *pair, recent] if retained else [*pair, {"role": "user", "content": "later task"}, old, recent, recent]
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "当前任务"},
        *body,
    ]
    original = copy.deepcopy(messages)
    with scripted_server([{"content": canonical_summary(ContextSummary(objective="继续当前任务"))}]) as (url, requests):
        platform = FakePlatform()
        loop = AgentLoop(platform=platform, llm=ModelTransport(url))
        loop._states["r"] = {"messages": messages, "user_message_index": 2, "delivered": {}}
        loop._steps["r"] = 0

        async def checkpoint(*args):
            pass

        monkeypatch.setattr(loop, "_checkpoint", checkpoint)
        turn = SimpleNamespace(
            messages=messages, run={"_attempt_id": "a", "_ledger_tokens": 0, "context_settings": {"threshold_ratio": 0.4}}
        )
        asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", []))
    assert requests[0]["messages"][:-1] == original
    result = ui_components({"names": ["Text"]}, {"messages": messages}, html_enabled=False)
    assert (result["components"] == "") is retained
    assert ("guide" in result) is not retained
    assert loop._states["r"]["context_boundary"]["reason"] == "compacted"


@pytest.mark.parametrize("mode", ["chat", "work"])
@pytest.mark.parametrize("updated", [False, True])
def test_file_skill_reuses_text_but_revalidates_and_installs_current_version(mode, updated):
    class Platform(FakePlatform):
        def __init__(self):
            super().__init__()
            self.checkpoints = {}
            self.reads = []

        async def get_checkpoint(self, run_id):
            return self.checkpoints.get(run_id)

        async def put_checkpoint(self, run_id, payload):
            step = self.checkpoints.get(run_id, {}).get("step", 0)
            self.checkpoints[run_id] = {"step": step + 1, "state": copy.deepcopy(payload["state"])}
            return {"step": step + 1}

        async def get_file_bytes(self, file_id, **kwargs):
            self.reads.append((file_id, kwargs["run_id"]))
            return f"---\nname: team-skill\ndescription: team rules\n---\nInstructions {file_id}".encode()

        async def get_run(self, run_id):
            version = "v2" if updated and run_id == "second" else "v1"
            run = {
                **await super().get_run(run_id),
                "options": {"mode": mode, "skill": "team-skill"},
                "personal_skills": [{"name": "team-skill", "file_id": version, "filename": "SKILL.md", "kind": "md"}],
            }
            if run_id == "second":
                state = self.checkpoints["first"]["state"]
                run["previous_model_context"] = {
                    "messages": state["messages"],
                    "tools": state["tool_catalog_v1"],
                    "prompt_anchors": state["prompt_anchors"],
                }
            return run

    execute = {"tool_calls": [{"index": 0, "id": "exec", "type": "function", "function": {"name": "exec", "arguments": '{"cmd":"true"}'}}]}
    responses = [{"content": "first"}, *([execute] if mode == "work" else []), {"content": "second"}]
    with scripted_server(responses) as (url, requests):
        platform, broker = Platform(), FakeBroker()
        loop = AgentLoop(platform=platform, broker=broker, llm=ModelTransport(url))
        asyncio.run(loop.process("first", attempt_id="a1"))
        asyncio.run(loop.process("second", attempt_id="a2"))
    assert [s for s, _ in platform.statuses if s == "succeeded"] == ["succeeded", "succeeded"]
    for before, after in pairwise(requests):
        assert_extension(before, after)
    loads = [m for m in requests[-1]["messages"] if m.get("name") == "load_skill"]
    assert len(loads) == (2 if updated else 1)
    # Only a sandbox needs the files again; the carried text is never re-read just to be resent.
    second = [("v2" if updated else "v1", "second")] if mode == "work" or updated else []
    assert platform.reads == [("v1", "first"), *second]
    if mode == "work":
        assert (
            "/workspace/.ailab/skills/team-skill/SKILL.md",
            f"---\nname: team-skill\ndescription: team rules\n---\nInstructions {'v2' if updated else 'v1'}".encode(),
        ) in broker.put_calls


@pytest.mark.parametrize("mode", ["chat", "work"])
@pytest.mark.parametrize("fmt", ["docx", "xlsx", "pptx", "pdf"])
def test_office_module_wire_load_reread_and_checkpoint_prefix(mode, fmt):
    from ailab_platform.adapters.runtime import restored_state
    from ailab_platform.agent.skill_modules import visible_modules

    class Platform(FakePlatform):
        def __init__(self):
            super().__init__()
            self.snapshots = []

        async def put_checkpoint(self, run_id, payload):
            result = await super().put_checkpoint(run_id, payload)
            self.snapshots.append(copy.deepcopy(self.checkpoint))
            return result

        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "options": {"mode": mode}}

    def load(number, **arguments):
        return {
            "tool_calls": [
                {
                    "index": 0,
                    "id": str(number),
                    "type": "function",
                    "function": {"name": "load_skill", "arguments": json.dumps({"name": f"platform.{fmt}", **arguments})},
                }
            ]
        }

    script = [
        load(1),
        load(2, resource="viewer/SKILL.md"),
        load(3, resource="viewer/SKILL.md"),
        load(4, resource="missing"),
        {"content": "done"},
    ]
    platform = Platform()
    with scripted_server(script) as (url, requests):
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert len(requests) == 5
    for before, after in pairwise(requests):
        assert_extension(before, after)
        assert before["tool_choice"] == after["tool_choice"] == "auto"
    results = {m.get("tool_call_id"): json.loads(m["content"]) for m in requests[-1]["messages"] if m["role"] == "tool"}
    assert "cursor" not in results["2"] and "next_cursor" not in results["2"]
    assert results["2"]["complete"] and results["3"]["already_loaded"] and "skill" not in results["3"]
    viewer = results["2"]["skill"]
    assert '"op":"get_view"' in viewer and '"op":"reveal"' in viewer
    assert "planned:P" not in viewer and "not currently callable" not in viewer
    assert "Ordinary composer messages do not automatically include the current selection" in viewer
    assert "file_id" in viewer and "targets" in viewer and 'source="viewport"' in viewer
    assert any(tool["function"]["name"] == "document_view" for tool in requests[0]["tools"])
    assert not results["4"]["ok"]
    assert visible_modules(requests[-1]["messages"])
    saved = platform.checkpoint["state"]
    restored = restored_state(platform.checkpoint)
    assert restored["messages"] == saved["messages"]
    assert visible_modules(restored["messages"]) == visible_modules(saved["messages"])
    platform.checkpoint = next(
        snapshot
        for snapshot in platform.snapshots
        if snapshot["state"]["messages"] == requests[2]["messages"] and not snapshot["state"]["pending"]
    )
    with scripted_server(script[2:]) as (url, resumed_requests):
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="resume"))
    assert resumed_requests[0]["messages"] == requests[2]["messages"]
    assert resumed_requests[0]["tools"] == requests[2]["tools"]
    assert resumed_requests[0]["tool_choice"] == requests[2]["tool_choice"]
    assert platform.statuses[-1][0] == "succeeded"
    prohibited = bytes((122, 99, 111, 100, 101)).decode()
    assert prohibited not in json.dumps(requests, ensure_ascii=False).lower()


@pytest.mark.parametrize("context_tokens, refused", [(24000, True), (32000, False), (64000, False)])
def test_office_resource_batch_wire_order_dedup_and_cumulative_budget(monkeypatch, context_tokens, refused):
    from ailab_platform.agent.skill_modules import read_module
    from ailab_platform.agent.tools import tool_result_message
    from ailab_platform.model_policy import ModelPolicy, estimator

    spec = ModelSpec("m", "m", provider="mock", context_tokens=context_tokens, max_output_tokens=512, capabilities=["tools"])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))

    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {
                **await super().get_run(run_id),
                "options": {"mode": "work"},
                "context_settings": {"auto_compact": False},
            }

    def call(index, call_id, resource=None):
        arguments = {"name": "platform.docx"}
        if resource:
            arguments["resource"] = resource
        return {
            "index": index,
            "id": call_id,
            "type": "function",
            "function": {"name": "load_skill", "arguments": json.dumps(arguments)},
        }

    first, second = "references/common-rules.md", "references/decorations.md"
    batch = [call(0, "first", first), call(1, "second", second), call(2, "duplicate", first)]
    platform = Platform()
    with scripted_server(
        [{"tool_calls": [call(0, "navigation")]}, {"tool_calls": batch}, {"content": "done"}],
        prompt_tokens=lambda request: estimator.estimate("m", {"messages": request["messages"], "tools": request["tools"]}),
    ) as (url, requests):
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert len(requests) == 3  # no model round trip between resources
    navigation = json.loads(next(m["content"] for m in requests[1]["messages"] if m.get("tool_call_id") == "navigation"))
    assert "同一次回复里并列调用 load_skill" in system_prompt_for("work")
    assert "同一次回复" not in navigation["skill"]
    assert "deps" in navigation["skill"]
    for before, after in pairwise(requests):
        assert_extension(before, after)
        assert json.dumps(after["messages"][: len(before["messages"])], ensure_ascii=False) == json.dumps(
            before["messages"], ensure_ascii=False
        )
        assert {k: v for k, v in before.items() if k != "messages"} == {k: v for k, v in after.items() if k != "messages"}
    appended = requests[-1]["messages"][len(requests[-2]["messages"]) :]
    assert [c["id"] for c in appended[0]["tool_calls"]] == ["first", "second", "duplicate"]
    assert [m["role"] for m in appended] == ["assistant", "tool", "tool", "tool"]
    assert [m["tool_call_id"] for m in appended[1:]] == ["first", "second", "duplicate"]
    results = [json.loads(m["content"]) for m in appended[1:]]
    assert results[0]["complete"] and results[0]["skill"] == read_module("platform.docx", "work", first)["skill"]
    assert results[2]["already_loaded"] and "skill" not in results[2]
    candidate = tool_result_message("second", "load_skill", read_module("platform.docx", "work", second))
    prefix = [*requests[-2]["messages"], appended[0]]
    reserve = min(2048, spec.max_output_tokens) + 256
    alone = estimator.estimate("m", {"messages": [*prefix, candidate], "tools": requests[-1]["tools"]}) + reserve
    cumulative = estimator.estimate("m", {"messages": [*prefix, appended[1], candidate], "tools": requests[-1]["tools"]}) + reserve
    assert alone < ModelPolicy.input_budget(spec)  # second resource fits without the first result
    if refused:
        from ailab_platform.agent.context_band import ContextBand, batch_budget

        band = ContextBand.for_model(spec, {"auto_compact": False})
        budget = batch_budget(band, prefix, requests[-1]["tools"], {"user_message_index": 2})
        assert band.cost([appended[0], appended[1], candidate]) > budget
        assert not results[1]["ok"] and "cannot fit" in results[1]["error"] and "skill" not in results[1]
    else:
        assert cumulative < ModelPolicy.input_budget(spec)
        assert results[1]["complete"] and results[1]["skill"] == read_module("platform.docx", "work", second)["skill"]


@pytest.mark.parametrize("path", ["in_turn", "cross_turn"])
@pytest.mark.parametrize("keep", [False, True])
def test_office_module_compaction_wire_and_visibility(monkeypatch, path, keep):
    from ailab_platform.agent.skill_modules import identity, read_module, visible_modules
    from tests.test_skill_modules import paired

    spec = ModelSpec("m", "m", provider="mock", context_tokens=14000, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    result = read_module("platform.docx", "work", "viewer/SKILL.md")
    tools = [next(t for t in tools_for_turn() if t["function"]["name"] == "load_skill")]
    platform = FakePlatform()
    with scripted_server([{"content": canonical_summary(ContextSummary(objective="summary"))}] * 3) as (url, requests):
        loop = AgentLoop(platform=platform, llm=ModelTransport(url))
        messages = [{"role": "system", "content": "rules"}, {"role": "system", "content": "context"}]

        async def checkpoint(*args):
            pass

        monkeypatch.setattr(loop, "_checkpoint", checkpoint)
        loop._states["r"] = {
            **fresh_state(),
            "messages": messages,
            "user_message_index": None,
            "delivered": {"/workspace/outputs/f.txt": 1},
        }
        loop._steps["r"] = 0
        run = {"_attempt_id": "a", "context_settings": {"threshold_ratio": 0.35}}
        for generation in range(3):
            messages.extend([{"role": "user", "content": "history"}])
            if keep:
                messages.append({"role": "assistant", "content": "旧" * 5000})
            if keep:
                messages.extend(
                    [{"role": "user", "content": "task"}, *paired(result, str(generation)), {"role": "assistant", "content": "continue"}]
                )
            else:
                messages.extend(
                    [
                        *paired(result, str(generation)),
                        {"role": "assistant", "content": "旧" * 5000},
                        {"role": "user", "content": "task"},
                        {"role": "assistant", "content": "continue"},
                        {"role": "assistant", "content": "continue"},
                    ]
                )
            original = copy.deepcopy(messages)
            if path == "in_turn":
                turn = SimpleNamespace(messages=messages, run=run)
                asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", tools))
            else:
                messages = asyncio.run(
                    loop._compact_parent_context("r", run, loop.llm, "m", messages, {"role": "user", "content": "next"}, tools)
                )
            wire = requests[generation]
            assert wire["messages"][:-1] == original
            assert wire["tools"] == tools and wire["tool_choice"] == "auto"
            assert (identity(result) in visible_modules(messages)) == keep
            summary = next(m["content"] for m in messages if m["role"] == "system" and "<loaded_modules>" in m.get("content", ""))
            assert result["hash"] in summary and result["resource"] in summary
            assert result["skill"] not in summary
            if path == "in_turn":
                assert loop._states["r"]["context_epoch"] == generation + 1
                assert loop._states["r"]["context_boundary"]["reason"] == "compacted"
            else:
                assert run["context_boundary"] == "compacted"


def test_office_no_snapshot_rebuild_keeps_navigation_not_visibility(monkeypatch):
    from ailab_platform.agent.skill_modules import annotate_summary, read_module, visible_modules
    from ailab_platform.contracts.context_summary import ContextSummary
    from tests.test_skill_modules import paired

    spec = ModelSpec("m", "m", provider="mock", context_tokens=14000, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    module = read_module("platform.docx", "chat", "viewer/SKILL.md")
    summary = annotate_summary(ContextSummary().model_dump_json(), paired(module), structured=True)

    class Platform(FakePlatform):
        async def get_model_context(self, run_id, after=None):
            return {
                "summary": summary,
                "through_run_id": "old",
                "history": [{"id": "recent", "prompt": "旧" * 6500, "reply": "done"}],
                "has_more": False,
            }

        async def save_model_summary(self, run_id, payload):
            self.saved = payload

    base = [{"role": "system", "content": "rules"}, {"role": "system", "content": "context"}, {"role": "user", "content": "current"}]
    with scripted_server([{"content": ContextSummary(objective="continue").model_dump_json()}]) as (url, requests):
        platform = Platform()
        loop = AgentLoop(platform=platform, llm=ModelTransport(url))
        run = {
            "_attempt_id": "a",
            "_fixed_catalog": tools_for_turn()[:1],
            "context_settings": {"threshold_ratio": 0.35},
            "options": {"capability_discovery": 1},
        }
        result = asyncio.run(loop._assemble_context("r", run, "m", base))
    assert requests[0]["messages"][:2] == base[:2]
    assert requests[0]["tool_choice"] == "auto"
    assert ContextSummary.model_validate_json(platform.saved["summary"]).read_modules[0].hash == module["hash"]
    assert not visible_modules(result)
    assert result[-1] == base[-1]
    assert run["context_boundary"] == "compacted"
    assert read_module("platform.docx", "chat", module["resource"])["hash"] == module["hash"]


def test_no_snapshot_rebuild_preserves_existing_revocation_boundary():
    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "parent_run_id": "parent", "context_boundary": "access_revoked"}

    platform = Platform()
    with scripted_server([{"content": "done"}]) as (url, requests):
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert any(kind == "agent.context_boundary" and payload["reason"] == "access_revoked" for kind, payload in platform.events)
    assert ("agent.context_boundary", {"reason": "rebuilt"}) not in platform.events
    assert requests[0]["tool_choice"] == "auto"


@pytest.mark.parametrize("vision", [False, True])
def test_artifact_review_keeps_recorded_prefix_and_discloses_visual_availability(monkeypatch, vision):
    import base64

    from ailab_platform.catalog import get_catalog

    monkeypatch.setitem(
        get_catalog().models,
        "m",
        ModelSpec("m", "m", provider="mock", context_tokens=200000, capabilities=frozenset({"tools", "vision"} if vision else {"tools"})),
    )

    class Broker(FakeBroker):
        async def review_artifact(self, lease_id, path, pages):
            return {
                "pages": pages or [1],
                "images": [base64.b64encode(b"\xff\xd8\xffreview\xff\xd9").decode()],
                "structural": {"issues": [], "sha256": "a" * 64},
                "page_count": 2,
                "coverage": {"reviewed_pages": pages or [1], "complete": False},
                "quality_gate": "pending_model_judgment",
            }

    def review(ident, page):
        return {
            "tool_calls": [
                {
                    "index": 0,
                    "id": ident,
                    "type": "function",
                    "function": {"name": "review_artifact", "arguments": json.dumps({"path": "/workspace/outputs/a.pdf", "pages": [page]})},
                }
            ]
        }

    with scripted_server([review("one", 1), review("two", 2), {"content": "reviewed"}]) as (url, requests):
        platform = FakePlatform()
        asyncio.run(AgentLoop(platform=platform, broker=Broker(), llm=ModelTransport(url)).process("fixture", attempt_id="attempt"))
    assert len(requests) == 3
    for before, after in pairwise(requests):
        assert_extension(before, after)
    assert all(any(t["function"]["name"] == "review_artifact" for t in r["tools"]) for r in requests)
    result = next(m for m in requests[1]["messages"] if m.get("tool_call_id") == "one")
    evidence = json.loads(result["content"])
    assert evidence["quality_gate"] == "pending_model_judgment"
    assert evidence["visual_review"] == ("pending_model_judgment" if vision else "no visual review available")
    images = [
        m for m in requests[1]["messages"] if isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])
    ]
    assert bool(images) == vision


def test_document_view_and_frozen_quotes_are_append_only_in_recorded_requests():
    from ailab_platform.agent.tools import tools_for_chat

    ref = {"file_id": "file", "version": "content-hash", "at": "p:2", "quote": "frozen selection", "filename": "report.docx"}

    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "doc_refs": [ref], "options": {"mode": "chat"}}

        async def document_view(self, run_id, payload):
            assert payload == {"op": "get_view"}
            return {"status": "unavailable", "views": []}

    call = {"index": 0, "id": "view", "type": "function", "function": {"name": "document_view", "arguments": '{"op":"get_view"}'}}
    with scripted_server([{"tool_calls": [call]}, {"content": "done"}]) as (url, requests):
        platform = Platform()
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("fixture", attempt_id="attempt"))
    assert platform.statuses[-1][0] == "succeeded"
    assert len(requests) == 2
    assert_extension(*requests)
    first = requests[0]
    user = next(m for m in first["messages"] if m["role"] == "user")
    assert "report.docx (p:2)" in user["content"] and "frozen selection" in user["content"]
    assert "frozen selection" not in json.dumps(first["tools"], ensure_ascii=False)
    assert "frozen selection" not in first["messages"][0]["content"]
    chat = next(t for t in tools_for_chat() if t["function"]["name"] == "document_view")
    work = next(t for t in tools_for_turn() if t["function"]["name"] == "document_view")
    assert chat == work


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_deploy_prompt_and_tool_changes_preserve_resumed_checkpoint(monkeypatch, mode):
    from ailab_platform.agent import loop as loop_module

    platform = FakePlatform()
    state = fresh_state()
    old_system = system_prompt_for(mode) + "\nold deployment"
    old_tools = copy.deepcopy(_tools_for_run({"model": "m", "options": {"mode": mode}}))
    next(t for t in old_tools if t["function"]["name"] == "load_skill")["function"]["description"] = "old loader description"
    state["messages"] = [
        {"role": "system", "content": old_system},
        {"role": "system", "content": ""},
        {"role": "user", "content": "continue"},
    ]
    state["tool_catalog_v1"] = old_tools
    state["user_message_index"] = 2
    platform.checkpoint = {"step": 1, "state": state}
    monkeypatch.setattr(loop_module, "system_prompt_for", lambda *a, **kw: "new deployment")
    with scripted_server([{"content": "done"}]) as (url, requests):
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="resume"))
    assert requests[0]["messages"] == state["messages"]
    assert requests[0]["tools"] == old_tools
    assert not any(e[0] == "agent.context_boundary" for e in platform.events)

    # A new run can adopt a deployment's rules only through a recorded boundary.
    previous = copy.deepcopy(platform.checkpoint["state"])
    platform.checkpoint = None

    async def next_run(run_id):
        return {
            "id": run_id,
            "user_id": "fixture",
            "model": "m",
            "status": "queued",
            "prompt": "next turn",
            "options": {"mode": mode},
            "previous_model_context": {"messages": previous["messages"], "tools": old_tools},
        }

    platform.get_run = next_run
    with scripted_server([{"content": "next answer"}]) as (url, next_requests):
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("next", attempt_id="next"))
    assert next_requests[0]["messages"][0]["content"] == "new deployment"
    assert next_requests[0]["tools"] != old_tools
    assert ("agent.context_boundary", {"reason": "model_changed"}) in platform.events


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_document_reveal_shape_error_allows_one_retry_with_fixed_recorded_tools(mode):
    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "options": {"mode": mode}}

        async def document_view(self, run_id, payload):
            assert payload == {"op": "reveal", "file_id": "file", "targets": [{"at": "page:1", "quote": "text"}]}
            return {"status": "sent"}

    def reveal(ident, args):
        return {
            "tool_calls": [
                {"index": 0, "id": ident, "type": "function", "function": {"name": "document_view", "arguments": json.dumps(args)}}
            ]
        }

    bad = {"op": "reveal", "file_id": "file", "filename": "report.pdf", "quote": "text"}
    good = {"op": "reveal", "file_id": "file", "targets": [{"at": "page:1", "quote": "text"}]}
    with scripted_server([reveal("bad", bad), reveal("retry", good), {"content": "done"}]) as (url, requests):
        platform = Platform()
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert len(requests) == 3
    for before, after in pairwise(requests):
        assert_extension(before, after)
    tool = next(t["function"] for t in requests[0]["tools"] if t["function"]["name"] == "document_view")
    assert "targets[{at, quote?}]" in tool["description"]
    error = next(m["content"] for m in requests[1]["messages"] if m.get("tool_call_id") == "bad")
    assert "targets[{at, quote?}]" in error and "filename" in error
    result = next(m["content"] for m in requests[2]["messages"] if m.get("tool_call_id") == "retry")
    assert json.loads(result)["status"] == "sent"


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_expired_capture_checkpoint_resumes_once_with_unavailable_note_and_boundary(monkeypatch, mode):
    from ailab_platform.clients import ClientError

    class Platform(FakePlatform):
        reads = 0

        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "options": {"mode": mode}}

        async def get_capture_image(self, run_id, capture_id):
            self.reads += 1
            raise ClientError("GET", "/capture", 410, "capture expired")

        async def document_view(self, run_id, payload):
            return {"status": "unavailable", "views": []}

    monkeypatch.setattr(
        "ailab_platform.agent.loop.get_catalog",
        lambda: Catalog([ModelSpec("m", "m", provider="mock", capabilities=frozenset({"tools", "vision"}))]),
    )
    file = {"capture_id": "expired", "filename": "文档截图", "page": 1, "content_type": "image/png"}
    original = image_part(b"\x89PNG\r\n\x1a\noriginal", "image/png")
    state = fresh_state()
    state["messages"] = [
        {"role": "system", "content": system_prompt_for(mode)},
        {"role": "system", "content": ""},
        {"role": "user", "content": [original]},
    ]
    state["tool_catalog_v1"] = _tools_for_run({"model": "m", "options": {"mode": mode}})
    state["user_message_index"] = 2
    platform = Platform()
    platform.checkpoint = {"step": 1, "state": checkpoint_state(state, {original["image_url"]["url"]: file})}
    call = {"index": 0, "id": "view", "type": "function", "function": {"name": "document_view", "arguments": '{"op":"get_view"}'}}
    with scripted_server([{"tool_calls": [call]}, {"content": "截图已过期，可重新打开文档。"}]) as (url, requests):
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="resume"))
    assert platform.statuses[-1][0] == "succeeded"
    assert platform.reads == 1 and len(requests) == 2
    assert_extension(*requests)
    assert "capture_expired" in json.dumps(requests[0]["messages"], ensure_ascii=False)
    assert "image_url" not in json.dumps(requests[0]["messages"])
    assert ("agent.context_boundary", {"reason": "rebuilt", "cause": "capture_expired"}) in platform.events
    assert "ailab_image" not in json.dumps(platform.checkpoint)


@pytest.mark.parametrize("status", [403, 404, 503])
def test_capture_restore_does_not_treat_authorization_or_dependency_failure_as_expiry(monkeypatch, status):
    from ailab_platform.clients import ClientError

    class Platform(FakePlatform):
        async def get_capture_image(self, run_id, capture_id):
            raise ClientError("GET", "/capture", status, "unavailable")

    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([ModelSpec("m", "m", capabilities=["vision"])]))
    state = fresh_state()
    state["messages"] = [
        {
            "role": "user",
            "content": [{"type": "ailab_image", "file": {"capture_id": "c", "filename": "capture", "content_type": "image/png"}}],
        }
    ]
    platform = Platform()
    with pytest.raises(BudgetPause, match="图片无法按原内容恢复"):
        asyncio.run(AgentLoop(platform=platform)._restore_images("r", {"model": "m"}, state))
    assert not platform.events


def test_chat_ten_turns_reuses_exact_epoch_wire_bytes():
    from ailab_platform.contracts.model_context import CONTEXT_ABI_VERSION

    class Platform(FakePlatform):
        async def get_checkpoint(self, run_id):
            return None

        async def put_checkpoint(self, run_id, payload):
            self.last_state = copy.deepcopy(payload["state"])
            return {"step": payload["expected_step"] + 1}

        async def get_run(self, run_id):
            run = {**await super().get_run(run_id), "options": {"mode": "chat"}, "prompt": run_id}
            if hasattr(self, "last_state"):
                state = self.last_state
                run["previous_model_context"] = {
                    "messages": state["messages"],
                    "tools": state["tool_catalog_v1"],
                    "context_epoch": state["context_epoch"],
                    "context_abi_version": state["context_abi_version"],
                }
            return run

    with scripted_server([{"content": f"answer {i}"} for i in range(10)]) as (url, requests):
        platform = Platform()
        loop = AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url))
        for i in range(10):
            asyncio.run(loop.process(f"turn {i}", attempt_id=f"a{i}"))
            assert platform.last_state["context_epoch"] == 0
            assert platform.last_state["context_abi_version"] == CONTEXT_ABI_VERSION
    assert len(requests) == 10
    for before, after in pairwise(requests):
        assert_extension(before, after)
    assert not any(kind == "agent.context_boundary" for kind, _ in platform.events)


def test_work_twenty_steps_strictly_extend_wire_bytes():
    calls = [_plan_call(i) for i in range(20)]
    for i, call in enumerate(calls):
        call["tool_calls"][0]["function"]["arguments"] = json.dumps({"steps": [f"step {i}"]})
    with scripted_server([*calls, {"content": "done"}]) as (url, requests):
        platform = FakePlatform()
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert len(requests) == 21
    for before, after in pairwise(requests):
        assert_extension(before, after)
    assert platform.checkpoint["state"]["context_epoch"] == 0


@pytest.mark.parametrize("abi", [None, 1, 999])
def test_parent_abi_match_exact_reuse_mismatch_rebuild(monkeypatch, abi):
    class Platform(FakePlatform):
        async def get_run(self, run_id):
            run = {**await super().get_run(run_id), "options": {"mode": "chat"}}
            run["previous_model_context"] = {
                "messages": [
                    {"role": "system", "content": system_prompt_for("chat", None, frozenset(), [], None, genui=False, genui_tools=False)},
                    {"role": "system", "content": ""},
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "answer"},
                ],
                "tools": _tools_for_run(run),
                "context_epoch": 8,
            }
            if abi is not None:
                run["previous_model_context"]["context_abi_version"] = abi
            self.previous = copy.deepcopy(run["previous_model_context"])
            return run

    with scripted_server([{"content": "done"}]) as (url, requests):
        platform = Platform()
        asyncio.run(AgentLoop(platform=platform, llm=ModelTransport(url)).process("r", attempt_id="a"))
    state = platform.checkpoint["state"]
    assert state["context_abi_version"] == 1
    if abi in (None, 1):
        assert requests[0]["messages"][:-1] == platform.previous["messages"]
        assert state["context_epoch"] == 8
    else:
        assert state["context_epoch"] == 9
        assert ("agent.context_boundary", {"reason": "context_abi_changed"}) in platform.events


def test_new_compaction_epoch_then_five_requests_extend(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=11000, max_output_tokens=1000)
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "旧" * 9000},
        {"role": "assistant", "content": "recent"},
    ]
    with scripted_server([{"content": canonical_summary(ContextSummary(objective="goal"))}, *[{"content": "next"}] * 6]) as (url, requests):
        loop = AgentLoop(platform=FakePlatform(), llm=ModelTransport(url))
        state = {**fresh_state(), "messages": messages, "user_message_index": 2, "context_epoch": 3}
        loop._states["r"] = state

        async def checkpoint(*args):
            assert state["context_epoch"] == 4

        monkeypatch.setattr(loop, "_checkpoint", checkpoint)
        run = {"_attempt_id": "a", "context_settings": {"threshold_ratio": 0.8}}
        tools = []

        async def exercise():
            await loop._compact_work_context("r", SimpleNamespace(messages=messages, run=run), loop.llm, "m", tools)
            assert state["context_epoch"] == 4
            for step in range(6):
                response = await loop._call_model(loop.llm, "r", run, "m", messages, step, tools)
                messages.extend([{"role": "assistant", "content": response.content}, {"role": "user", "content": f"continue {step}"}])

        asyncio.run(exercise())
    assert len(requests) == 7
    assert requests[1]["messages"] != requests[0]["messages"][: len(requests[1]["messages"])]
    for before, after in pairwise(requests[1:]):
        assert_extension(before, after)
    assert all(request.get("tools") == requests[0].get("tools") for request in requests)


@pytest.mark.parametrize("failure", ["length", "invalid_json", "transport"])
def test_summary_wire_failure_preserves_epoch_and_running_handle(monkeypatch, failure):
    from ailab_platform.config import settings

    monkeypatch.setattr(settings, "llm_retry_backoff_seconds", 0)
    spec = ModelSpec("m", "m", provider="mock", context_tokens=22000, max_output_tokens=4096)
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    script = (
        [{"status": 503}] * 3
        if failure == "transport"
        else [{"content": "{", "_finish_reason": "length" if failure == "length" else "stop"}]
    )
    with scripted_server(script) as (url, requests):
        loop = AgentLoop(FakePlatform(), llm=ModelTransport(url))
        state = {
            **fresh_state(),
            "context_epoch": 7,
            "user_message_index": 2,
            "background_jobs": ["live"],
            "messages": [
                {"role": "system", "content": "rules"},
                {"role": "system", "content": "context"},
                {"role": "user", "content": "task"},
                {"role": "assistant", "content": "old" * 7000},
            ],
        }
        loop._states["r"] = state
        before = copy.deepcopy(state)
        turn = SimpleNamespace(messages=state["messages"], run={"_attempt_id": "a", "context_settings": {"threshold_ratio": 0.2}})
        with pytest.raises(BudgetPause):
            asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", []))
    assert {key: value for key, value in state.items() if key != "prompt_anchors"} == before
    assert len(requests) == (3 if failure == "transport" else 1)
    assert all(request["messages"][:-1] == before["messages"] for request in requests)
    assert all(request["max_tokens"] >= 4096 for request in requests)
    assert not any(kind == "agent.context_boundary" for kind, _ in loop.platform.events)


@pytest.mark.parametrize("scope", ["run", "parent", "rebuild"])
def test_band_boundary_wire_changes_are_confined_to_new_epoch(monkeypatch, scope):
    from ailab_platform.agent.context_band import ContextBand
    from tests.test_context_band import CURRENT, PREFIX, fixture

    spec = ModelSpec("band", "band", provider="mock", context_tokens=64000, max_output_tokens=8192, capabilities={"tools"})
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    tools = [{"type": "function", "function": {"name": "example", "parameters": {"type": "object"}}}]

    class Platform(FakePlatform):
        async def get_model_context(self, *args, **kwargs):
            return {
                "summary": "",
                "through_run_id": None,
                "history": [{"id": str(i), "prompt": "汉" * 1900, "reply": "reply"} for i in range(32)],
                "has_more": False,
            }

        async def save_model_summary(self, *args):
            pass

    with scripted_server(
        [{"content": canonical_summary(ContextSummary(objective="checkpoint"))}, {"content": "first"}, {"content": "second"}]
    ) as (url, requests):
        loop = AgentLoop(platform=Platform(), llm=ModelTransport(url))
        messages = fixture(64000)
        original = copy.deepcopy(messages)
        state = {**fresh_state(), "messages": messages, "user_message_index": 2}
        loop._states["r"] = state
        run = {"model": "band", "_attempt_id": "a", "_fixed_catalog": tools}
        manager = loop._context("r")

        async def checkpoint(*args):
            pass

        monkeypatch.setattr(loop, "_checkpoint", checkpoint)

        async def exercise():
            nonlocal messages
            if scope == "run":
                await manager.compact("r", SimpleNamespace(messages=messages, run=run), loop.llm, "band", tools)
            elif scope == "parent":
                messages = await manager._compact_parent_context("r", run, loop.llm, "band", messages, CURRENT, tools)
            else:
                messages = await manager._assemble_context("r", run, "band", [*PREFIX, CURRENT])
            await loop._call_model(loop.llm, "r", run, "band", messages, 1, tools=tools)
            messages.extend([{"role": "assistant", "content": "first"}, {"role": "user", "content": "continue"}])
            await loop._call_model(loop.llm, "r", run, "band", messages, 2, tools=tools)

        asyncio.run(exercise())
    assert len(requests) == 3
    if scope != "rebuild":
        assert requests[0]["messages"][:-1] == original  # summary is an exact extension of the old epoch
    assert requests[1]["tools"] == requests[0]["tools"] == requests[2]["tools"] == tools
    assert requests[0]["tool_choice"] == requests[1]["tool_choice"] == requests[2]["tool_choice"] == "auto"
    assert requests[1]["messages"][:2] == PREFIX
    assert ContextBand.for_model(spec).cost({"messages": requests[1]["messages"], "tools": tools}) <= 48000
    assert_extension(requests[1], requests[2])
    # Every intentional replacement is boundary-owned: bounded semantic/runtime/module
    # blocks, current-user placement, selected complete raw groups, refreshed memory.
    # No tool-schema, tool-choice or reasoning-policy change is used to make it fit.
    assert {key: value for key, value in requests[1].items() if key not in {"messages", "max_tokens"}} == {
        key: value for key, value in requests[2].items() if key not in {"messages", "max_tokens"}
    }


@pytest.mark.parametrize("reason", ["model_changed", "context_abi_changed", "rebuilt", "access_revoked"])
@pytest.mark.parametrize("page_size", [3, 20])
def test_below_trigger_rebuild_validates_checkpoint_and_first_wire_request(monkeypatch, reason, page_size):
    from ailab_platform.agent.context_band import ContextBand
    from ailab_platform.clients import ClientError
    from ailab_platform.contracts.model_context import CONTEXT_ABI_VERSION

    spec = ModelSpec("m", "m", provider="mock", context_tokens=1000000, max_output_tokens=8192, capabilities={"tools"})
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    history = [{"id": str(i), "prompt": "汉" * 10000, "reply": "ok"} for i in range(10)]

    class Platform(FakePlatform):
        async def get_run(self, run_id):
            run = {**await super().get_run(run_id), "parent_run_id": "old", "options": {"mode": "chat"}}
            if reason != "rebuilt":
                prior = [{"role": "system", "content": "old model rules"}, {"role": "system", "content": "context"}]
                if reason == "access_revoked":
                    prior += [{"role": "tool", "name": "recall_history", "content": '{"run_id":"gone"}'}]
                run["previous_model_context"] = {
                    "messages": prior,
                    "tools": [],
                    "context_epoch": 7,
                    "context_abi_version": 0 if reason == "context_abi_changed" else CONTEXT_ABI_VERSION,
                }
            return run

        async def recall(self, *args):
            raise ClientError("POST", "/recall", 404, "gone")

        async def get_model_context(self, run_id, after=None):
            start = int(after) + 1 if after else 0
            return {
                "summary": "",
                "through_run_id": None,
                "history": history[start : start + page_size],
                "has_more": start + page_size < 10,
            }

        async def save_model_summary(self, run_id, payload):
            self.saved_summary = payload

    with scripted_server([{"content": canonical_summary(ContextSummary(objective="goal"))}, _plan_call(1), {"content": "done"}]) as (
        url,
        requests,
    ):
        platform = Platform()
        asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert len(requests) == 3
    band = ContextBand.for_model(spec)
    for messages in (requests[1]["messages"], platform.checkpoint["state"]["messages"]):
        total = band.cost({"messages": messages, "tools": requests[1]["tools"]})
        assert band.lower <= total <= band.upper <= band.ceiling
    assert platform.saved_summary
    boundaries = [payload for kind, payload in platform.events if kind == "agent.context_boundary"]
    assert boundaries[0]["reason"] == reason
    assert boundaries[0]["after_tokens"] <= band.upper
    assert_extension(requests[1], requests[2])


def test_usage_anchor_metadata_does_not_change_recorded_request_bytes(monkeypatch):
    from ailab_platform.agent.context_usage import record_usage

    captures = []
    for anchored in (False, True):
        monkeypatch.setattr("ailab_platform.agent.loop.record_usage", record_usage if anchored else lambda *_: None)
        with scripted_server([_plan_call(1), {"content": "done"}]) as (url, requests):
            platform = FakePlatform()
            asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url)).process("r", attempt_id="a"))
            assert platform.statuses[-1][0] == "succeeded"
        assert_extension(*requests)
        captures.append(json.dumps(requests, ensure_ascii=False))
        assert bool(platform.checkpoint["state"].get("prompt_anchors")) is anchored
    assert captures[0] == captures[1]
