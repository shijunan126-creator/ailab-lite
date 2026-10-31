"""The request prefix stays byte-stable, so the provider's prefix cache can serve it.

DeepSeek (and OpenAI-compatible backends) cache the longest previously seen prefix of the rendered
prompt, and the tool schemas render before every message. A changed tool list, a rewritten earlier
turn or a context block that moves ahead of history makes everything after it a cache miss.
"""

import asyncio
import itertools
import json

import httpx
from fastapi.testclient import TestClient

from ailab_platform.agent.loop import AgentLoop
from ailab_platform.api.main import app
from ailab_platform.clients import AsyncPlatformClient
from ailab_platform.llm import LLMClient, LLMResponse, ToolCall
from tests.run_claims import attempt_headers
from tests.test_agent_loop import FakeBroker
from tests.test_conversations import AUTH

UI_REPLY = '第一轮完成\n```openui\nroot = Callout("info", "提示", "内容")\n```'


class Recorder(LLMClient):
    def __init__(self, calls, script):
        self.calls, self.script = calls, list(script)

    async def chat(self, messages, tools, model, user_id=None):
        if "会话标题" in str(messages[0].get("content")):
            return LLMResponse(content="标题", usage={"prompt_tokens": 1, "completion_tokens": 1})
        self.calls[-1].append({"messages": json.loads(json.dumps(messages)), "tools": json.dumps(tools, ensure_ascii=False)})
        item = self.script.pop(0)
        usage = {"prompt_tokens": 100, "completion_tokens": 10}
        if isinstance(item, str):
            return LLMResponse(content=item, reasoning_content="想一想", usage=usage)
        return LLMResponse(tool_calls=[ToolCall(f"c{len(self.calls[-1])}", *item)], reasoning_content="调工具", usage=usage)


def _conversation(turns, mode="work", setup=None):
    calls = []

    async def turn(c, conv, prompt, script):
        run = c.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": prompt, "mode": mode}).json()
        platform = AsyncPlatformClient("http://testserver", token="test-service")
        await platform._http.aclose()
        platform._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        try:
            calls.append([])
            owner = attempt_headers(c, run["id"])
            await AgentLoop(platform=platform, broker=FakeBroker(), llm=Recorder(calls, script)).process(
                run["id"], attempt_id=owner["X-Run-Attempt"]
            )
            assert c.get(f"/v1/runs/{run['id']}", headers=AUTH).json()["status"] == "succeeded"
        finally:
            await platform.aclose()

    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()["id"]
        for index, (prompt, script) in enumerate(turns):
            if setup:
                setup(c, index)
            asyncio.run(turn(c, conv, prompt, script))
    return calls


def _extends(earlier, later):
    return later["tools"] == earlier["tools"] and later["messages"][: len(earlier["messages"])] == earlier["messages"]


def test_discovering_tools_mid_run_keeps_every_request_an_extension_of_the_last():
    (calls,) = _conversation(
        [("查一下网页再整理成表格", [("search_capabilities", {"query": "网页 搜索"}), ("search_capabilities", {"query": "表格"}), "完成"])]
    )
    assert len(calls) == 3
    assert all(_extends(a, b) for a, b in itertools.pairwise(calls))


def test_a_new_turn_reuses_the_previous_turns_request_up_to_its_user_message():
    first, second, third = _conversation(
        [
            ("第一个问题", [("search_capabilities", {"query": "执行"}), UI_REPLY]),
            ("第二个问题", ["第二轮完成"]),
            ("第三个问题", ["第三轮完成"]),
        ]
    )
    # Every turn starts with the same tools, and the history it renders is exactly what later turns render.
    assert first[0]["tools"] == second[0]["tools"] == third[0]["tools"]
    shared = len(second[0]["messages"]) - 1  # everything before the new user message
    assert third[0]["messages"][:shared] == second[0]["messages"][:shared]
    assert {"role": "assistant", "content": UI_REPLY} in third[0]["messages"]
