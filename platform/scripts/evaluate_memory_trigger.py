#!/usr/bin/env python3
"""Evaluate real trigger decisions with production prompts/tools; externally configured credentials."""

import argparse
import asyncio
import json
from pathlib import Path

from ailab_platform.agent.loop import _tools_for_run
from ailab_platform.agent.prompts import memory_prompt, system_prompt_for
from ailab_platform.catalog import get_catalog
from ailab_platform.llm import llm_for


def trigger_request(model, memory, prompt):
    spec = get_catalog().get(model)
    system = system_prompt_for(
        mode="chat",
        genui=bool(spec and spec.supports({"genui"})),
        genui_tools=bool(spec and spec.supports({"tools"})),
    )
    return [
        {"role": "system", "content": system},
        {"role": "system", "content": memory_prompt(memory)},
        {"role": "user", "content": prompt},
    ], _tools_for_run({"model": model, "options": {"mode": "chat"}})


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
    semaphore = asyncio.Semaphore(3)

    async def case(index, item):
        async with semaphore:
            messages, tools = trigger_request(model, memory, item["query"])
            response = await llm_for(model).chat(
                messages,
                tools,
                model,
                user_id="memory-synthetic-evaluation",
                max_tokens=8192,
            )
        searched = any(
            (c.name == "memory" and c.arguments.get("action") == "search") or c.name == "recall_history" for c in response.tool_calls
        )
        return {
            "index": index,
            "expected": item["should_search"],
            "searched": searched,
            "decisions": [
                {"tool": call.name, "action": call.arguments.get("action"), "arguments": call.arguments} for call in response.tool_calls
            ],
            "usage": response.usage,
        }

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
    result = asyncio.run(evaluate(args.model or get_catalog().default_id()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["search_rate"] >= 0.85 and result["false_search_rate"] <= 0.10 else 1)
