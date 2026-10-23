"""The Agent service for the browser tests: the real service, with one scripted model behaviour added.

A prompt containing 需要提问 makes the model run a command in the sandbox, ask the user a question, and finish with
the answer. Everything else is the mock model unchanged. llm.py is not a test seam, so only the model lookup is
replaced, and only in this process. Queue, claims, leases, the Broker and the sandbox are the real ones.
"""

from __future__ import annotations

import asyncio
import json

from ailab_platform.agent import loop
from ailab_platform.agent.main import app
from ailab_platform.llm import LLMClient, LLMError, LLMResponse, MockLLM, ToolCall

TRIGGER = "需要提问"
BEFORE_ID, ASK_ID = "exec_before", "ask_version"
OPTIONS = ["标准版", "精简版"]
STREAM_TRIGGER = "验证流式衔接"
STREAM_TEXT = "环境已就绪：python-docx 和中文字体可用。下面开始编写文档生成脚本。"
stream_release = asyncio.Event()


@app.post("/e2e/release-stream")
async def release_stream():
    stream_release.set()
    return {"ok": True}


def _last_user_text(messages: list[dict]) -> str:
    return next((m["content"] for m in reversed(messages) if m.get("role") == "user" and isinstance(m.get("content"), str)), "")


class QuestionThenAnswer(LLMClient):
    def __init__(self) -> None:
        self.mock = MockLLM()

    async def chat_stream(self, messages, tools, model, user_id=None, on_text=None, on_reasoning=None):
        if STREAM_TRIGGER != _last_user_text(messages):
            return await super().chat_stream(messages, tools, model, user_id, on_text, on_reasoning)
        stream_release.clear()
        if on_text:
            await on_text("环境")
            await on_text(STREAM_TEXT[2:])
        # The browser releases the model after it sees the narrative through the real SSE proxy.
        await asyncio.wait_for(stream_release.wait(), timeout=30)
        return LLMResponse(content=STREAM_TEXT, usage={"prompt_tokens": 20, "completion_tokens": 50})

    async def chat(self, messages, tools, model, user_id=None):
        if _last_user_text(messages) == "验证失败状态":
            raise LLMError("故障验收")
        if TRIGGER not in _last_user_text(messages):
            return await self.mock.chat(messages, tools, model, user_id)
        answered = {m["tool_call_id"]: m for m in messages if m.get("role") == "tool"}
        if BEFORE_ID not in answered:
            return LLMResponse(
                content="先检查环境。", tool_calls=[ToolCall(BEFORE_ID, "exec", {"cmd": "echo before-question > /workspace/note.txt"})]
            )
        if ASK_ID not in answered:
            question = {"question": "要哪个版本？", "options": OPTIONS, "allow_text": False}
            return LLMResponse(content="需要先确认一下版本。", tool_calls=[ToolCall(ASK_ID, "ask_user", question)])
        answer = json.loads(answered[ASK_ID]["content"])["answer"]
        return LLMResponse(content=f"好的，按「{answer}」继续完成。")


loop.llm_for = lambda model: QuestionThenAnswer()
