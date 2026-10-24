"""Model gateway client (OpenAI-compatible) plus a deterministic mock.

The mock keeps the whole platform runnable without a DeepSeek key; switching
``AILAB_LLM_PROVIDER=openai`` points the same code at Higress/GPUStack.
"""

from __future__ import annotations

import asyncio  # noqa: F401 - legacy callers patch the shared asyncio module
import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from .catalog import get_catalog
from .config import settings

log = logging.getLogger("ailab.llm")


class LLMError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.output_started = False


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"
    reasoning_content: str = ""
    thinking_seconds: float = 0.0  # filled in by the agent loop from when the reasoning streamed


@dataclass
class ModelCall:
    call_id: str
    run_id: str
    max_output_tokens: int
    reached_model: bool = False
    actual_backend: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    output_bytes: int = 0
    emitted: bool = False
    ttft_seconds: float | None = None
    quiet: bool = False


model_call: ContextVar[ModelCall | None] = ContextVar("model_call", default=None)


def _capture_response(response: LLMResponse) -> None:
    call = model_call.get()
    if call is not None:
        call.usage = response.usage
        call.output_bytes = len((response.content + response.reasoning_content).encode()) + sum(
            len(json.dumps(c.arguments, ensure_ascii=False).encode()) + len(c.name.encode()) for c in response.tool_calls
        )


TextSink = Callable[[str], Awaitable[None]]


class LLMClient:
    async def chat(self, messages: list[dict], tools: list[dict], model: str, user_id: str | None = None) -> LLMResponse:
        raise NotImplementedError

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
        user_id: str | None = None,
        on_text: TextSink | None = None,
        on_reasoning: TextSink | None = None,
    ) -> LLMResponse:
        """Like chat(), but hands the model's thinking (on_reasoning) and then the answer text (on_text) over as they
        are produced. Default: in pieces once the whole response is there."""
        call = model_call.get()
        if call is not None:
            call.reached_model = True
        response = await self.chat(messages, tools, model, user_id=user_id)
        _capture_response(response)
        if on_reasoning and response.reasoning_content:
            for i in range(0, len(response.reasoning_content), 24):
                await on_reasoning(response.reasoning_content[i : i + 24])
        if on_text and response.content:
            step = 24
            for i in range(0, len(response.content), step):
                await on_text(response.content[i : i + step])
        return response


RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
INVALID_ARGUMENTS_KEY = "_invalid_arguments"


def _parse_tool_calls(raw_calls: list[dict]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for i, raw in enumerate(raw_calls):
        fn = raw.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            # Never run a tool on guessed arguments (write_file would write an empty file).
            args = {INVALID_ARGUMENTS_KEY: fn.get("arguments")}
        calls.append(
            ToolCall(
                id=raw.get("id") or f"call_{int(time.time())}_{i}",
                name=fn.get("name") or "",
                arguments=args if isinstance(args, dict) else {INVALID_ARGUMENTS_KEY: fn.get("arguments")},
            )
        )
    return calls


def _usage(usage: dict) -> dict[str, int]:
    if not usage:
        return {}
    result = {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "cached_tokens": _cached_tokens(usage),
    }
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if reasoning is not None:
        result["reasoning_tokens"] = int(reasoning)
    return result


class _StreamState:
    """Accumulates an OpenAI-style chat completion stream into one LLMResponse."""

    def __init__(self) -> None:
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.calls: dict[int, dict] = {}
        self.usage: dict = {}
        self.finish = "stop"
        self.completed = False
        self.emitted = False  # some text already reached the user

    def feed(self, chunk: dict) -> tuple[str, str]:
        """Take one chunk; return the (answer text, thinking) it adds."""
        if chunk.get("error"):  # vLLM reports a failure mid-stream as an error chunk with HTTP 200
            raise LLMError(f"model stream failed: {str(chunk['error'])[:300]}", retryable=True)
        call = model_call.get()
        if chunk.get("usage"):
            self.usage = chunk["usage"]
            if call is not None:
                call.usage = _usage(self.usage)
        if call is not None and chunk.get("model"):
            call.actual_backend = str(chunk["model"])[:120]
        text = thinking = ""
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            reasoning = next(
                (delta[k] for k in ("reasoning_content", "reasoning", "thinking") if isinstance(delta.get(k), str) and delta[k]), ""
            )
            if not reasoning:
                reasoning = "".join(
                    str(item.get("text") or item.get("summary") or "")
                    for item in delta.get("reasoning_details") or []
                    if isinstance(item, dict)
                )
            if call is not None:
                call.output_bytes += len((delta.get("content", "") or "").encode())
                call.output_bytes += len(reasoning.encode())
                for tool in delta.get("tool_calls") or []:
                    call.output_bytes += sum(len(str(v).encode()) for v in (tool.get("function") or {}).values())
            if reasoning:
                self.reasoning.append(reasoning)
                thinking += reasoning
            if delta.get("content"):
                self.content.append(delta["content"])
                text += delta["content"]
            for tc in delta.get("tool_calls") or []:
                acc = self.calls.setdefault(int(tc.get("index", 0)), {"id": "", "function": {"name": "", "arguments": ""}})
                acc["id"] = tc.get("id") or acc["id"]
                fn = tc.get("function") or {}
                acc["function"]["name"] += fn.get("name") or ""
                acc["function"]["arguments"] += fn.get("arguments") or ""
            if choice.get("finish_reason"):
                self.finish = choice["finish_reason"]
                self.completed = True
        return text, thinking

    def response(self) -> LLMResponse:
        if self.finish == "length":
            raise LLMError("model output was truncated (finish_reason=length); raise max tokens or shorten the task")
        return LLMResponse(
            content="".join(self.content),
            tool_calls=_parse_tool_calls([self.calls[i] for i in sorted(self.calls)]),
            usage=_usage(self.usage),
            finish_reason=self.finish,
            reasoning_content="".join(self.reasoning),
        )


def _cached_tokens(usage: dict) -> int:
    """Prompt tokens served from the upstream's cache, whichever way it reports them."""
    cached = usage.get("prompt_cache_hit_tokens")
    if cached is None:
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    try:
        return max(0, int(cached or 0))
    except (TypeError, ValueError):
        return 0


def _assistant_steps(messages: list[dict]) -> int:
    """Tool rounds since the user's latest message (earlier turns in the history do not count)."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=0)
    return sum(1 for m in messages[last_user:] if m.get("role") == "assistant" and m.get("tool_calls"))


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content if part.get("type") == "text") if isinstance(content, list) else ""


def _user_prompt(messages: list[dict]) -> str:
    return next((_content_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")


def _wants_ppt(prompt: str) -> bool:
    text = prompt.lower()
    return any(k in text for k in ("ppt", "幻灯片", "演示文稿", "汇报材料", "做个演示"))


def _wants_xlsx(prompt: str) -> bool:
    text = prompt.lower()
    return any(k in text for k in ("excel", "xlsx", "表格", "报表"))


def _call(step: int, tool: str, arguments: dict) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(id=f"call_mock_{step}_{tool}", name=tool, arguments=dict(arguments))])


# Each scripted task: the tool calls in order, then the closing message.
_PPT = [
    ("load_skill", {"name": "pptx"}),
    ("exec", {"cmd": "mkdir -p /workspace/work && python /opt/skills/pptx/scripts/build.py --example > /workspace/work/deck.json"}),
    ("exec", {"cmd": "python /opt/skills/pptx/scripts/build.py /workspace/work/deck.json /workspace/outputs/ailab-demo.pptx"}),
    ("deliver_file", {"path": "/workspace/outputs/ailab-demo.pptx", "filename": "ailab-demo.pptx"}),
]
_XLSX = [
    ("load_skill", {"name": "xlsx"}),
    ("exec", {"cmd": "mkdir -p /workspace/work && python /opt/skills/xlsx/scripts/build_xlsx.py --example > /workspace/work/book.json"}),
    ("exec", {"cmd": "python /opt/skills/xlsx/scripts/build_xlsx.py /workspace/work/book.json /workspace/outputs/sales.xlsx"}),
    ("deliver_file", {"path": "/workspace/outputs/sales.xlsx", "filename": "sales.xlsx"}),
]


class MockLLM(LLMClient):
    """Deterministic tool-calling policy used by tests and the dev demo."""

    async def chat(self, messages: list[dict], tools: list[dict], model: str, user_id: str | None = None) -> LLMResponse:
        prompt = _user_prompt(messages)
        step = _assistant_steps(messages)
        usage = {"prompt_tokens": max(1, len(json.dumps(messages, ensure_ascii=False)) // 4), "completion_tokens": 20}

        script, closing = None, ""
        system = str(messages[0].get("content") or "") if messages else ""
        picked = next((k for k in ("pptx", "xlsx") if f"本轮已选技能：{k}" in system), "")
        if "exec" not in {t.get("function", {}).get("name") for t in tools or []}:  # no sandbox offered (chat): words only
            pass
        elif picked == "pptx" or _wants_ppt(prompt):
            script, closing = _PPT, "已完成：演示文稿已生成并交付到资料库，右侧可以预览。想调整哪一页、换什么配色，直接告诉我。"
        elif picked == "xlsx" or _wants_xlsx(prompt):
            script, closing = _XLSX, "表格已生成并交付：包含三个区域的月度数据、合计行和柱状图。"
        elif "文件" in prompt or "总结" in prompt:
            content = f"# 处理结果\n\n原始请求：{prompt}\n\n这是 Agent Service 生成的文件。\n"
            script = [
                ("write_file", {"path": "/workspace/outputs/answer.md", "content": content}),
                ("deliver_file", {"path": "/workspace/outputs/answer.md", "filename": "answer.md"}),
            ]
            closing = "文件已生成并交付。"
        elif "沙箱" in prompt or "命令" in prompt:
            script = [("exec", {"cmd": "echo 'hello from ailab sandbox'; pwd; ls -la"})]
            closing = "沙箱命令已执行完成。"

        if script is None:  # plain chat: answer directly, no tools
            turns = sum(1 for m in messages if m.get("role") == "user" and not _content_text(m.get("content")).startswith("（平台提示"))
            attached = prompt.count("（附件《")
            text = (
                f"你好！这是**离线演示模型**的回答（本对话第 {turns} 轮）。\n\n"
                "- 它不调用真实大模型，只用来演示完整链路\n"
                "- 说「做一个 PPT」或「做一个 Excel 表格」可以看到沙箱生成文件和实时预览\n\n"
                f"> 你刚才说：{prompt.split(chr(10))[0][:80]}" + (f"\n\n我读到了 {attached} 个附件的内容。" if attached else "")
            )
            thinking = f"用户说：「{prompt[:40]}」。这是离线演示模型，不需要工具，按固定模板直接回答，并提示可以试试做 PPT 或表格。"
            return LLMResponse(content=text, usage=usage, reasoning_content=thinking)
        if step < len(script):
            name, arguments = script[step]
            response = _call(step, name, arguments)
            response.usage = usage
            return response
        return LLMResponse(content=closing, usage=usage)


@cache
def llm_for(model_id: str) -> LLMClient:
    """The client for a catalog model: its own upstream URL, key and upstream model name."""
    spec = get_catalog().get(model_id)
    if spec is None or not spec.available:
        raise LLMError(f"model {model_id!r} is not available")
    if spec.provider == "mock":
        return MockLLM()
    from .model_transport import ModelTransport

    return ModelTransport(
        spec.base_url,
        spec.api_key(),
        spec.upstream_model or spec.id,
        echo_reasoning=spec.echo_reasoning or settings.llm_echo_reasoning,
        disable_reasoning_for_quiet=spec.disable_reasoning_for_quiet,
    )


def reset_llm_cache() -> None:
    llm_for.cache_clear()


def __getattr__(name):
    # Keep legacy imports without a cycle when transport is imported directly.
    if name in {"ModelTransport", "OpenAICompatibleClient"}:
        from .model_transport import ModelTransport

        return ModelTransport
    raise AttributeError(name)
