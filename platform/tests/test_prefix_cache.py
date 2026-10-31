import asyncio
import copy
import json

from ailab_platform.agent.discovery import REDUCED_V1, catalog_for_budget
from ailab_platform.agent.loop import AgentLoop, _tools_for_run
from ailab_platform.agent.prompts import memory_prompt
from ailab_platform.agent.tools import tools_for_turn
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
