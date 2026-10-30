"""P6 real transport runner. Raw answers only in an external temporary directory."""

# ruff: noqa: E402 -- the standalone script adds the platform import root

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import tomllib
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLATFORM))
from cases import CASES

from ailab_platform.agent.prompts import system_prompt_for
from ailab_platform.agent.tools import UI_COMPONENTS, ToolError, tool_result_message, ui_components
from ailab_platform.config import settings
from ailab_platform.domain.answer_parts import for_history
from ailab_platform.llm import MockLLM, ModelCall, model_call
from ailab_platform.model_transport import ModelTransport


def load_key():
    """Use the existing personal deployment key without ever serializing it."""
    if not os.environ.get("AILAB_KEY_DEEPSEEK"):
        from dotenv import dotenv_values

        source = Path("/Users/michael/Documents/AILab-local/config/deepseek.env")
        if source.is_file():
            value = dotenv_values(source).get("AILAB_KEY_DEEPSEEK")
            if value:
                os.environ["AILAB_KEY_DEEPSEEK"] = value
    return bool(os.environ.get("AILAB_KEY_DEEPSEEK"))


class ToolRoundLimit(RuntimeError):
    pass


async def answer_with_tools(client, messages, model, record):
    """Use the Agent's local tool with fresh checkpoint-equivalent state per Run."""
    state = {}
    record.update(tool_called=False, tool_call_count=0, tool_round_count=0, component_names=[], usage={})
    for round_number in range(4):
        response = await client.chat_stream(messages, [UI_COMPONENTS], model)
        for key, value in response.usage.items():
            record["usage"][key] = record["usage"].get(key, 0) + value
        if not response.tool_calls:
            return response
        record["tool_called"] = True
        record["tool_call_count"] += len(response.tool_calls)
        record["tool_round_count"] += 1
        for call in response.tool_calls:
            names = call.arguments.get("names")
            if isinstance(names, list):
                record["component_names"].extend(n for n in names if isinstance(n, str))
        if round_number == 3:
            raise ToolRoundLimit("more than three tool rounds")
        assistant = {
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in response.tool_calls
            ],
        }
        if response.reasoning_content:
            assistant["reasoning_content"] = response.reasoning_content
        messages.append(assistant)
        for call in response.tool_calls:
            if call.name != "ui_components":
                raise ValueError("unsupported evaluation tool")
            try:
                result = ui_components(call.arguments, state, html_enabled=settings.genui_html_enabled)
            except ToolError as exc:
                result = {"ok": False, "error": str(exc)}
            messages.append(tool_result_message(call.id, call.name, result))


async def main(args):
    specs = tomllib.loads((PLATFORM / "models.toml").read_text())["models"]
    spec = next(x for x in specs if x["id"] == args.model)
    if spec["provider"] != "mock" and not load_key():
        raise SystemExit("Missing AILAB_KEY_DEEPSEEK in process environment or personal config/deepseek.env")
    settings.genui_enabled = settings.genui_html_enabled = True
    settings.llm_idle_timeout_seconds = 90
    settings.llm_total_timeout_seconds = 240
    settings.ttft_timeout_seconds = 90
    settings.llm_max_retries = 0
    system = system_prompt_for(mode="chat", genui=True, deferred=True, genui_tools=True)
    client = (
        MockLLM()
        if spec["provider"] == "mock"
        else ModelTransport(
            spec["base_url"], os.environ[spec["api_key_env"]], spec["upstream_model"], echo_reasoning=spec.get("echo_reasoning", False)
        )
    )
    output = Path(args.output).resolve()
    if output.is_relative_to(PLATFORM.parent):
        raise SystemExit("Raw answers must be outside the repository")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    answer_file = output / f"{args.label}.jsonl"
    if answer_file.exists():
        raise SystemExit("Refusing to overwrite an existing run")
    metadata = dict(
        model=spec["id"],
        upstream_model=spec.get("upstream_model"),
        provider=spec["provider"],
        temperature=0.2,
        system_sha256=hashlib.sha256(system.encode()).hexdigest(),
        system_chars=len(system),
        max_tokens=32768,
        concurrency=3,
        connect_seconds=5,
        read_seconds=90,
        total_seconds=240,
    )
    (output / f"{args.label}-meta.json").write_text(json.dumps(metadata, indent=2))
    sem = asyncio.Semaphore(3)

    async def run(case):
        async with sem:
            messages = [{"role": "system", "content": system}]
            for turn, user in enumerate([case["prompt"]] + ([case["edit"]] if case["edit"] else []), 1):
                if args.model == "ailab-demo":
                    user += "\n[[genui-interactive]]"
                messages.append(dict(role="user", content=user))
                started = time.perf_counter()
                token = model_call.set(ModelCall(f"eval-{args.label}-{case['id']}-{turn}", "", 32768))
                row = dict(case=case["id"], kind=case["kind"], turn=turn, model=spec["id"])
                try:
                    async with asyncio.timeout(245):
                        response = await answer_with_tools(client, [dict(m) for m in messages], spec["id"], row)
                    row.update(answer=response.content, finish_reason=response.finish_reason)
                    # Same recent-turn history policy as prompts.history: no reasoning or tool transcript.
                    messages.append(dict(role="assistant", content=for_history(response.content, keep_ui=True, limit=16000)))
                except Exception as exc:
                    # Do not log provider bodies, headers, credentials or exception repr.
                    row.update(answer="", error=type(exc).__name__)
                finally:
                    model_call.reset(token)
                row["latency_seconds"] = round(time.perf_counter() - started, 3)
                with answer_file.open("a") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"{case['id']} turn={turn} {row['latency_seconds']}s {row.get('error', row.get('finish_reason'))}", flush=True)
                if "error" in row:
                    break

    selected = [c for c in CASES if not args.cases or c["id"] in args.cases.split(",")]
    if args.cases and len(selected) != len(set(args.cases.split(","))):
        raise SystemExit("Unknown case id")
    await asyncio.gather(*(run(case) for case in selected))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--output", default="/tmp/ailab-genui-eval")
    parser.add_argument("--label", default="initial")
    parser.add_argument("--cases", help="Comma-separated ids; only for one failing-case rerun")
    asyncio.run(main(parser.parse_args()))
