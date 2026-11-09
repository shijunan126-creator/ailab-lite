#!/usr/bin/env python3
"""Evaluate real trigger decisions with production prompts/tools; externally configured credentials."""

import argparse
import asyncio
import json
from pathlib import Path

from ailab_platform.agent.prompts import memory_prompt, system_prompt_for
from ailab_platform.agent.tools import tools_for_chat
from ailab_platform.catalog import get_catalog
from ailab_platform.llm import llm_for


async def evaluate(model):
    spec = get_catalog().get(model)
    if spec is None or spec.provider == "mock" or not spec.available:
        raise ValueError("real available model required")
    fixture = json.loads((Path(__file__).resolve().parents[1] / "tests/golden/memory_v2.json").read_text())
    memory = {
        "entries": [
            {
                "id": "mem_trigger",
                "tier": "archive",
                "kind": "project",
                "page": "项目",
                "content": "合成评测背景",
                "origin": "manual",
                "entities": ["Atlas", "Bluefin", "Orion", "数据库", "部署", "集群", "公司", "规划"],
            }
        ]
    }
    system = system_prompt_for(mode="chat") + memory_prompt(memory)
    semaphore = asyncio.Semaphore(3)

    async def case(index, item):
        async with semaphore:
            response = await llm_for(model).chat(
                [{"role": "system", "content": system}, {"role": "user", "content": item["query"]}],
                tools_for_chat(),
                model,
                user_id="memory-synthetic-evaluation",
                max_tokens=8192,
            )
        searched = any(
            (c.name == "memory" and c.arguments.get("action") == "search") or c.name == "recall_history" for c in response.tool_calls
        )
        return {"index": index, "expected": item["should_search"], "searched": searched, "usage": response.usage}

    results = await asyncio.gather(*(case(i, c) for i, c in enumerate(fixture["trigger"])))
    yes = [r for r in results if r["expected"]]
    no = [r for r in results if not r["expected"]]
    return {
        "model": model,
        "should_search": sum(r["searched"] for r in yes),
        "should_search_cases": len(yes),
        "search_rate": sum(r["searched"] for r in yes) / len(yes),
        "false_searches": sum(r["searched"] for r in no),
        "should_not_search_cases": len(no),
        "false_search_rate": sum(r["searched"] for r in no) / len(no),
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(evaluate(args.model or get_catalog().default_id())), ensure_ascii=False, indent=2))
