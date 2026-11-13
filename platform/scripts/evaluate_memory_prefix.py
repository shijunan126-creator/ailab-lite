#!/usr/bin/env python3
"""Serial real-model prefix acceptance: unchanged, item changes, and a simulated day boundary.

API/Broker are in-memory doubles. Uses actual AgentLoop and serialized provider requests;
this measures model caching, not live service/sandbox integration. Configure credentials
externally. Each scenario's warm cached/prompt ratio must reach 95%. Each call must
cache the old prefix within one configured cache block; no retries or padding.
"""

import argparse
import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta

from ailab_platform.agent.loop import AgentLoop
from ailab_platform.catalog import get_catalog
from ailab_platform.llm import llm_for
from ailab_platform.memory_recall import policy
from tests.prefix_harness import assert_extension
from tests.test_agent_loop import FakeBroker, FakePlatform

QUESTIONS = ["请用一句话解释什么是哈希表。", "用一句话说明它的查找速度。", "再用一句话说一个使用场景。", "用一句话总结。"]


def cache_acceptance(usages, first_turn_calls, cache_block_tokens=128):
    """Use provider token counts, never a local tokenizer, to price the appended suffix.

    128 is the observed block quantum of the configured OpenCode DeepSeek endpoint.
    Other endpoints must supply their own block size; this is not a universal limit.
    """
    if cache_block_tokens < 1:
        raise ValueError("cache block must be positive")
    warm = usages[first_turn_calls:]
    prompt = sum(u.get("prompt_tokens", 0) for u in warm)
    cached = sum(u.get("cached_tokens", 0) for u in warm)
    checks = []
    for index in range(1, len(usages)):
        previous, current = usages[index - 1], usages[index]
        old_prompt = previous.get("prompt_tokens", 0)
        new_prompt = current.get("prompt_tokens", 0)
        hit = current.get("cached_tokens", 0)
        added = new_prompt - old_prompt
        miss = new_prompt - hit
        checks.append(
            {
                "call": index + 1,
                "appended_tokens": added,
                "uncached_tokens": miss,
                "old_prefix_uncached_tokens": max(0, old_prompt - hit),
                "passed": old_prompt > 0 and added >= 0 and 0 <= hit <= new_prompt and miss <= added + cache_block_tokens - 1,
            }
        )
    return {
        "cached_tokens": cached,
        "prompt_tokens": prompt,
        "ratio": cached / prompt if prompt else 0,
        "warm_call_ratios": [u.get("cached_tokens", 0) / u["prompt_tokens"] if u.get("prompt_tokens") else 0 for u in warm],
        "cache_block_tokens": cache_block_tokens,
        "prefix_cache_checks": checks,
        "passed": bool(warm) and prompt > 0 and cached / prompt >= 0.95 and bool(checks) and all(c["passed"] for c in checks),
    }


def scenarios():
    initial = {
        "revision": 1,
        "enabled": True,
        "entries": [
            {
                "id": "mem_core",
                "kind": "preference",
                "origin": "explicit",
                "content": "回答先给结论",
                "observed_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": "mem_project",
                "kind": "project",
                "origin": "explicit",
                "content": "正在推进Atlas项目",
                "observed_at": "2026-01-01T00:00:00+00:00",
            },
        ],
    }
    changed = copy.deepcopy(initial)
    changed["revision"] = 2
    changed["entries"] = [
        changed["entries"][1],
        {
            "id": "mem_new",
            "kind": "preference",
            "origin": "manual",
            "content": "回答说明适用条件",
            "observed_at": "2026-01-02T00:00:00+00:00",
        },
    ]
    return {"unchanged": [initial] * 4, "items_changed": [initial, initial, changed, changed], "day_boundary": [initial] * 4}


class Platform(FakePlatform):
    def __init__(self, mode, model, case, snapshots):
        super().__init__()
        self.checkpoints = {}
        self.mode, self.model, self.case = mode, model, case
        self.snapshots = copy.deepcopy(snapshots)

    async def get_memory_context(self, run_id):
        return copy.deepcopy(self.snapshots[int(run_id[-1])])

    async def memory(self, run_id, request):
        if request.get("action") == "list":
            return await self.get_memory_context(run_id)
        return {"results": [], "status": "NO_RELEVANT_MEMORY", "revision": self.snapshots[int(run_id[-1])]["revision"]}

    async def recall(self, *args, **kwargs):
        return {"results": []}

    async def get_checkpoint(self, run_id):
        return self.checkpoints.get(run_id)

    async def put_checkpoint(self, run_id, payload):
        step = self.checkpoints.get(run_id, {}).get("step", 0)
        assert step == payload["expected_step"]
        self.checkpoints[run_id] = {"step": step + 1, "state": copy.deepcopy(payload["state"])}
        return {"step": step + 1}

    async def get_run(self, run_id):
        index = int(run_id[-1])
        run = {
            **await super().get_run(run_id),
            "model": self.model,
            "conversation_id": f"conv_memory_cache_{self.mode}_{self.case}",
            "prompt": QUESTIONS[index],
            "options": {"mode": self.mode},
            "memory": await self.get_memory_context(run_id),
        }
        if index:
            state = self.checkpoints[f"turn{index - 1}"]["state"]
            run["previous_model_context"] = {
                "messages": state["messages"],
                "tools": state["tool_catalog_v1"],
                "prompt_anchors": state.get("prompt_anchors", []),
                "memory_observed": state["memory_observed"],
                "context_abi_version": state["context_abi_version"],
            }
        return run


class Recorder:
    def __init__(self, model):
        self.inner = llm_for(model)
        self.requests, self.usages, self.sessions = [], [], []
        self.original_request = self.inner._request
        spec = get_catalog().get(model)

        def record(*args, **kwargs):
            payload, headers = self.original_request(*args, **kwargs)
            self.requests.append(copy.deepcopy(payload))
            self.sessions.append(headers.get(spec.session_header) if spec.session_header else None)
            return payload, headers

        self.inner._request = record

    def close(self):
        self.inner._request = self.original_request

    async def chat_stream(self, *args, **kwargs):
        result = await self.inner.chat_stream(*args, **kwargs)
        self.usages.append(result.usage)
        return result

    async def chat(self, *args, **kwargs):
        result = await self.inner.chat(*args, **kwargs)
        self.usages.append(result.usage)
        return result


async def evaluate(model, cache_block_tokens=128):
    spec = get_catalog().get(model)
    if not spec or not spec.available or spec.provider == "mock":
        raise ValueError("real available model required")
    out = {}
    original_clock = policy.datetime

    class Clock(datetime):
        instant = datetime(2026, 11, 9, 23, 59, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.instant if tz is None else cls.instant.astimezone(tz)

    try:
        for mode in ["chat", "work"]:
            for case, snapshots in scenarios().items():
                platform, recorder = Platform(mode, model, case, snapshots), Recorder(model)
                loop = AgentLoop(platform=platform, broker=FakeBroker(), llm=recorder)
                boundaries = []
                policy.datetime = Clock if case == "day_boundary" else original_clock
                Clock.instant = datetime(2026, 11, 9, 23, 59, tzinfo=UTC)
                try:
                    for index in range(4):
                        if case == "day_boundary" and index == 2:
                            Clock.instant += timedelta(days=1)
                        await loop.process(f"turn{index}", attempt_id=f"a{index}")
                        boundaries.append(len(recorder.usages))
                    for previous, current in zip(recorder.requests, recorder.requests[1:]):
                        assert_extension(previous, current)
                        for key in ("tool_choice", "thinking", "model", "temperature"):
                            assert previous.get(key) == current.get(key), key
                    assert len(set(recorder.sessions)) == 1
                    assert all(r["messages"][:2] == recorder.requests[0]["messages"][:2] for r in recorder.requests)
                    assert len(recorder.requests) == len(recorder.usages)
                    acceptance = cache_acceptance(recorder.usages, boundaries[0], cache_block_tokens)
                    completed = sum(s == "succeeded" for s, _ in platform.statuses)
                    out[f"{mode}/{case}"] = {
                        **acceptance,
                        "calls": recorder.usages,
                        "turn_call_boundaries": boundaries,
                        "prefix_extensions": True,
                        "stable_session": True,
                        "completed_turns": completed,
                        "passed": completed == 4 and acceptance["passed"],
                    }
                finally:
                    recorder.close()
    finally:
        policy.datetime = original_clock
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--cache-block-tokens", type=int, default=128, help="Configured endpoint's cache quantum (OpenCode DeepSeek: 128)")
    args = parser.parse_args()
    result = asyncio.run(evaluate(args.model or get_catalog().default_id(), args.cache_block_tokens))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if all(r["passed"] for r in result.values()) else 1)
