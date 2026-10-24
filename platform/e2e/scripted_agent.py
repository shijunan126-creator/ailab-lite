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
FEATURE_STREAM = "集成思考与早标题"
FEATURE_ANSWER = "这是过程框外的流式最终回答。"
FEATURE_TITLE = "集成测试生成标题"
feature_versions: dict[str, int] = {}
title_release = asyncio.Event()
workspace_release = asyncio.Event()
workspace_seen: dict[str, list[dict]] = {}


@app.post("/e2e/workspace-release")
async def release_workspace():
    workspace_release.set()
    return {"ok": True}


@app.get("/e2e/workspace-seen")
async def seen_workspace():
    return workspace_seen


@app.post("/e2e/release-title")
async def release_title():
    title_release.set()
    return {"ok": True}


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
        if _last_user_text(messages) == FEATURE_STREAM:
            stream_release.clear()
            if on_reasoning:
                await on_reasoning("**分析问题**\n先确认目标和约束。\n\n**组织回答**\n逐项核对证据。")
            if on_text:
                await on_text(FEATURE_ANSWER)
            await asyncio.wait_for(stream_release.wait(), timeout=30)
            return LLMResponse(content=FEATURE_ANSWER, usage={"prompt_tokens": 20, "completion_tokens": 30})
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
        prompt = _last_user_text(messages)
        original = next(
            (
                m["content"]
                for m in reversed(messages)
                if m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].startswith("工作区")
            ),
            "",
        )
        if original:
            workspace_seen[original] = messages
            if original.startswith("工作区保持") and not any(m.get("role") == "tool" for m in messages):
                workspace_release.clear()
                await asyncio.wait_for(workspace_release.wait(), timeout=45)
                return LLMResponse(
                    tool_calls=[ToolCall("workspace_step", "exec", {"cmd": "echo workspace"})],
                    usage={"prompt_tokens": 20, "completion_tokens": 10},
                )
            if original.startswith("工作区失败"):
                workspace_release.clear()
                await asyncio.wait_for(workspace_release.wait(), timeout=45)
                raise LLMError("workspace intentional failure")
            if original.startswith(("工作区保持", "工作区说明", "工作区续发")):
                return LLMResponse(content="工作区完成：" + prompt, usage={"prompt_tokens": 20, "completion_tokens": 10})
        if messages and "概括用户问题生成" in str(messages[0].get("content")):
            if json.loads(prompt).get("question") == FEATURE_STREAM:
                await asyncio.wait_for(title_release.wait(), timeout=8)
                return LLMResponse(content=f"**{FEATURE_TITLE}**", usage={"prompt_tokens": 10, "completion_tokens": 10})
        if prompt.startswith("集成版本"):
            feature_versions[prompt] = feature_versions.get(prompt, 0) + 1
            return LLMResponse(
                content=f"{prompt}：第 {feature_versions[prompt]} 次生成。", usage={"prompt_tokens": 40, "completion_tokens": 20}
            )
        if prompt.startswith("集成页码引用 "):
            file_id = prompt.split()[1]
            return LLMResponse(content=f"参见[《a.pdf》第2页](ailab-file://{file_id}?page=2)。")
        if _last_user_text(messages) == "验证账号记忆工具":
            results = [json.loads(m["content"]) for m in messages if m.get("role") == "tool" and m.get("name") == "memory"]
            if not results:
                return LLMResponse(tool_calls=[ToolCall("memory_list", "memory", {"action": "list"})])
            if len(results) == 1:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(
                            "memory_add",
                            "memory",
                            {
                                "action": "add",
                                "kind": "user",
                                "content": "验收偏好：回答保持简洁",
                                "explicit": True,
                                "expected_revision": results[-1]["revision"],
                            },
                        )
                    ]
                )
            return LLMResponse(content="已通过记忆工具保存偏好。")
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
