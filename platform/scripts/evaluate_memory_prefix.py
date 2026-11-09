#!/usr/bin/env python3
"""Four real-model AgentLoop turns with isolated in-memory API/Broker doubles.

This checks prompt caching, not live API/Broker/sandbox integration. Credentials and catalog
must be supplied externally. Only synthetic prompts and provider usage are emitted.
"""

import argparse
import asyncio
import copy
import json

from ailab_platform.agent.loop import AgentLoop
from ailab_platform.catalog import get_catalog
from ailab_platform.llm import llm_for
from tests.prefix_harness import assert_extension
from tests.test_agent_loop import FakeBroker, FakePlatform

QUESTIONS = ["请用一句话解释什么是哈希表。", "用一句话说明它的查找速度。", "再用一句话说一个使用场景。", "用一句话总结。"]


class Platform(FakePlatform):
    def __init__(self, mode, model):
        super().__init__()
        self.checkpoints = {}
        self.mode, self.model, self.prior = mode, model, None

    async def get_memory_context(self, run_id):
        return {"entries": [], "enabled": True, "revision": 0}

    async def memory(self, run_id, request):
        return {"results": [], "status": "NO_RELEVANT_MEMORY", "revision": 0}

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
        run = {
            **await super().get_run(run_id),
            "model": self.model,
            "conversation_id": "conv_memory_cache_" + self.mode,
            "prompt": QUESTIONS[int(run_id[-1])],
            "options": {"mode": self.mode},
            "memory": await self.get_memory_context(run_id),
        }
        if self.prior:
            state = self.checkpoints[self.prior]["state"]
            run["previous_model_context"] = {
                "messages": state["messages"],
                "tools": state["tool_catalog_v1"],
                "prompt_anchors": state["prompt_anchors"],
                "memory_observed": state.get("memory_observed", {}),
                "context_abi_version": state.get("context_abi_version"),
            }
        return run


class Recorder:
    def __init__(self, model):
        self.inner = llm_for(model)
        self.requests, self.usages = [], []

    async def chat_stream(self, messages, tools, model, *args, **kwargs):
        self.requests.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        result = await self.inner.chat_stream(messages, tools, model, *args, **kwargs)
        self.usages.append(result.usage)
        return result

    async def chat(self, *args, **kwargs):
        return await self.inner.chat(*args, **kwargs)


async def evaluate(model):
    spec = get_catalog().get(model)
    if not spec or not spec.available or spec.provider == "mock":
        raise ValueError("real available model required")
    out = {}
    for mode in ["chat", "work"]:
        platform, recorder = Platform(mode, model), Recorder(model)
        loop = AgentLoop(platform=platform, broker=FakeBroker(), llm=recorder)
        boundaries = []
        for index in range(4):
            run_id = "cache_" + mode + str(index)
            await loop.process(run_id, attempt_id="a" + str(index))
            platform.prior = run_id
            boundaries.append(len(recorder.usages))
        for previous, current in zip(recorder.requests, recorder.requests[1:]):
            assert_extension(previous, current)
        usage = recorder.usages[boundaries[0] :]
        prompt = sum(u.get("prompt_tokens", 0) for u in usage)
        cached = sum(u.get("cached_tokens", 0) for u in usage)
        out[mode] = {
            "calls": recorder.usages,
            "cached_tokens": cached,
            "prompt_tokens": prompt,
            "ratio": cached / prompt if prompt else 0,
            "prefix_extensions": True,
            "completed_turns": sum(s == "succeeded" for s, _ in platform.statuses),
        }
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(evaluate(args.model or get_catalog().default_id())), ensure_ascii=False, indent=2))
