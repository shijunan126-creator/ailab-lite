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
    assert "truncated" in result["content"] and len(result["content"]) < 100_000


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
                run["previous_model_context"] = {"messages": state["messages"], "tools": state["tool_catalog_v1"]}
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
    assert_extension(requests[1], requests[2])
    assert requests[2]["messages"] == [
        *requests[1]["messages"],
        {"role": "assistant", "content": "first answer", "reasoning_content": "answer reasoning"},
        {"role": "user", "content": "second"},
    ]


def test_parent_near_threshold_is_summarized_as_prefix_extension(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=6000, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    previous = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "旧" * 4700},
        {"role": "assistant", "content": "done"},
    ]
    current = {"role": "user", "content": "新" * 500}
    tools = [{"type": "function", "function": {"name": "example", "parameters": {"type": "object"}}}]
    with scripted_server([{"content": "summary"}]) as (url, requests):
        platform = FakePlatform()
        llm = ModelTransport(url)
        run = {"_attempt_id": "attempt", "options": {"mode": "chat"}}
        loop = AgentLoop(platform=platform, llm=llm)
        result = asyncio.run(loop._compact_parent_context("fixture", run, llm, "m", previous, current, tools))
    assert requests[0]["messages"][:-1] == previous
    assert requests[0]["tools"] == tools and requests[0]["tool_choice"] == "none"
    assert result[:2] == previous[:2] and result[2]["content"].endswith("summary") and result[-1] == current
    assert ("agent.context_boundary", {"reason": "compacted"}) in platform.events


def test_parent_with_recall_compacts_with_instruction_to_forget_excerpts(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=6000, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    prior = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "旧" * 4700},
        {"role": "tool", "name": "recall_history", "tool_call_id": "recall", "content": "{}"},
    ]
    current = {"role": "user", "content": "新" * 500}
    with scripted_server([{"content": "曾查询历史主题"}]) as (url, requests):
        loop = AgentLoop(platform=FakePlatform(), llm=ModelTransport(url))
        result = asyncio.run(loop._compact_parent_context("r", {"_attempt_id": "a"}, loop.llm, "m", prior, current, []))
    assert "不要把 recall_history 的原文" in requests[0]["messages"][-1]["content"]
    assert all(message.get("name") != "recall_history" for message in result)


def test_run_compaction_extends_wire_prefix_and_retains_complete_groups(monkeypatch):
    spec = ModelSpec("m", "m", provider="mock", context_tokens=6000, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": "旧" * 5100},
        {"role": "assistant", "content": "old"},
        {"role": "assistant", "content": "recent one"},
        {"role": "tool", "tool_call_id": "x", "content": "result"},
        {"role": "assistant", "content": "recent two"},
    ]
    original = copy.deepcopy(messages)
    tools = [{"type": "function", "function": {"name": "example", "parameters": {"type": "object"}}}]
    with scripted_server([{"content": "summary"}]) as (url, requests):
        platform = FakePlatform()
        loop = AgentLoop(platform=platform, llm=ModelTransport(url))
        loop._states["r"] = {"user_message_index": 2, "delivered": {"/workspace/outputs/report.txt": 12}}
        loop._steps["r"] = 0

        async def checkpoint(*args):
            pass

        monkeypatch.setattr(loop, "_checkpoint", checkpoint)
        turn = SimpleNamespace(messages=messages, run={"_attempt_id": "a", "_ledger_tokens": 0, "prompt": "constraints"})
        try:
            asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", tools))
        except BudgetPause:
            pass  # Still examine the request made before a non-converging compaction paused.
    assert requests[0]["messages"][:-1] == original
    assert requests[0]["tools"] == tools and requests[0]["tool_choice"] == "none"
    assert messages[:3] == original[:3]
    assert messages[3]["content"].endswith("summary")
    assert "report.txt" in messages[4]["content"] and "/workspace/outputs/report.txt" in messages[4]["content"]
    assert messages[5:] == original[-3:]
    assert loop._states["r"]["user_message_index"] == 2
    assert ("agent.context_boundary", {"reason": "compacted"}) in platform.events


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

        async def get_memory_context(self, run_id):
            return {}

        async def recall(self, run_id, request):
            if self.revoked:
                raise ClientError("POST", "/recall", 404, "gone")
            return {"run_id": request["run_id"]}

    async def exercise():
        platform = Platform()
        loop = AgentLoop(platform=platform)
        messages = [{"role": "tool", "name": "recall_history", "content": '{"results":[{"run_id":"source","text":"old"}]}'}]
        original = copy.deepcopy(messages)
        await loop._model_attempt(ScriptedLLM([LLMResponse(content="ok")]), "r", {"_attempt_id": "a"}, "m", messages, 0, tools=[])
        assert messages == original
        platform.revoked = True
        await loop._model_attempt(ScriptedLLM([LLMResponse(content="ok")]), "r", {"_attempt_id": "a"}, "m", messages, 1, tools=[])
        assert json.loads(messages[0]["content"]) == {"ok": False, "error": "引用的历史已不可用"}
        assert ("agent.context_boundary", {"reason": "access_revoked"}) in platform.events

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
        for step in (0, -2, -3):
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
    assert [(mode, kind) for mode, kind, _ in observed] == [("chat", "main"), ("chat", "title"), ("chat", "compaction")]
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
    assert ("agent.context_boundary", {"reason": "access_revoked"}) in platform.events
    assert "secret" not in json.dumps(requests[0], ensure_ascii=False)
    assert any(m.get("content") == "valid" for m in requests[0]["messages"])
    assert broker.leases == 0 and not broker.put_calls


def test_rebuilt_history_summary_appends_instruction_to_packed_history(monkeypatch):
    from ailab_platform.agent.loop import estimator

    monkeypatch.setattr(estimator, "factors", {})
    spec = ModelSpec("m", "m", provider="mock", context_tokens=6000, max_output_tokens=512, capabilities=frozenset({"tools"}))
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
    with scripted_server([{"content": "summary"}]) as (url, requests):
        platform = Platform()
        loop = AgentLoop(platform=platform, llm=ModelTransport(url))
        result = asyncio.run(loop._assemble_context("r", {"_attempt_id": "a", "_fixed_catalog": tools}, "m", base))
    assert requests[0]["messages"][:-1] == [*base[:2], {"role": "user", "content": "旧" * 5300}, {"role": "assistant", "content": "done"}]
    assert requests[0]["tools"] == tools and requests[0]["tool_choice"] == "none"
    assert result[:2] == base[:2] and result[-1] == base[-1] and result[2]["content"].endswith("summary")
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


def test_checkpoint_continue_keeps_previous_request_prefix(monkeypatch):
    from ailab_platform.config import settings

    class Platform(FakePlatform):
        window = "first"

        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "budget_window_id": self.window}

    platform = Platform()
    with scripted_server([_plan_call(1), {"content": "paused summary"}, {"content": "done"}]) as (url, requests):
        monkeypatch.setattr(settings, "max_agent_steps", 1)
        loop = AgentLoop(platform=platform, broker=FakeBroker(), llm=ModelTransport(url))
        asyncio.run(loop.process("r", attempt_id="first"))
        assert platform.paused is not None
        platform.window = "second"
        monkeypatch.setattr(settings, "max_agent_steps", 4)
        asyncio.run(loop.process("r", attempt=2, attempt_id="second"))
    assert platform.statuses[-1][0] == "succeeded"
    assert_extension(requests[0], requests[1])
    assert requests[1]["tool_choice"] == "none"
    assert_extension(requests[1], requests[2])
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
    from ailab_platform.agent.loop import estimator

    monkeypatch.setattr(estimator, "factors", {})
    spec = ModelSpec("m", "m", provider="mock", context_tokens=6000, max_output_tokens=512, capabilities=frozenset({"tools"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    old = {"revision": 1, "entries": [{"id": "old", "content": "旧专属事实"}]}
    new = {"revision": 2, "entries": [{"id": "new", "content": "最新"}]}
    prior = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "project" + memory_prompt(old)},
        {"role": "user", "content": "旧" * 4700},
        {"role": "assistant", "content": "done"},
    ]

    class Platform(FakePlatform):
        async def get_memory_context(self, run_id):
            return new

    with scripted_server([{"content": "summary"}]) as (url, requests):
        loop = AgentLoop(platform=Platform(), llm=ModelTransport(url))
        result = asyncio.run(
            loop._compact_parent_context("r", {"_attempt_id": "a"}, loop.llm, "m", prior, {"role": "user", "content": "新" * 500}, [])
        )
    assert requests[0]["messages"][:-1] == prior
    assert "旧专属事实" not in result[1]["content"] and "最新" in result[1]["content"]
    assert loop._states["r"]["memory_observed"]["revision"] == 2


def test_compaction_drops_old_image_and_preserves_recent_image(monkeypatch):
    from ailab_platform.agent.loop import estimator
    from ailab_platform.config import settings

    monkeypatch.setattr(estimator, "factors", {})
    monkeypatch.setattr(settings, "vision_input_tokens", 16)
    spec = ModelSpec("m", "m", provider="mock", context_tokens=6000, max_output_tokens=512, capabilities=frozenset({"tools", "vision"}))
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: Catalog([spec]))
    old_image, recent_image = image_part(b"old image", "image/png"), image_part(b"recent image", "image/png")
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": "context"},
        {"role": "user", "content": [{"type": "text", "text": "旧" * 5100}, old_image]},
        {"role": "assistant", "content": "old"},
        {"role": "assistant", "content": "recent one"},
        {"role": "tool", "tool_call_id": "x", "content": "result"},
        {"role": "assistant", "content": "recent two"},
        {"role": "user", "content": [recent_image]},
    ]
    original = copy.deepcopy(messages)
    with scripted_server([{"content": "summary"}]) as (url, requests):
        loop = AgentLoop(platform=FakePlatform(), llm=ModelTransport(url))
        loop._states["r"] = {}

        async def checkpoint(*args):
            pass

        monkeypatch.setattr(loop, "_checkpoint", checkpoint)
        asyncio.run(loop._compact_work_context("r", SimpleNamespace(messages=messages, run={"_attempt_id": "a"}), loop.llm, "m", []))
    assert requests[0]["messages"][:-1] == original
    assert messages[3:] == original[-4:]
    assert old_image not in [part for m in messages if isinstance(m.get("content"), list) for part in m["content"]]
    assert messages[-1]["content"] == [recent_image]


def test_rebuilt_summary_does_not_fallback_to_a_different_model(monkeypatch):
    from ailab_platform.agent.loop import estimator
    from ailab_platform.config import settings
    from ailab_platform.llm import LLMError

    monkeypatch.setattr(estimator, "factors", {})
    monkeypatch.setattr(settings, "llm_max_retries", 0)
    first = ModelSpec("m", "m", provider="mock", context_tokens=6000, max_output_tokens=512, fallback=["backup"])
    second = ModelSpec("backup", "backup", provider="mock", context_tokens=6000, max_output_tokens=512)
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
    with pytest.raises(LLMError, match="unavailable"):
        asyncio.run(
            loop._assemble_context(
                "r",
                {"_attempt_id": "a", "_fixed_catalog": []},
                "m",
                [{"role": "system", "content": "rules"}, {"role": "user", "content": "current"}],
            )
        )
    assert not fallback and not any(kind == "agent.model_fallback" for kind, _ in platform.events)
