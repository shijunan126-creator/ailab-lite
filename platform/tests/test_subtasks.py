"""run_subtasks forks the parent's request, runs children concurrently and hands their reports back."""

import asyncio
import json

from ailab_platform.agent import subtasks
from ailab_platform.llm import LLMClient, LLMResponse, ToolCall
from tests.test_agent_loop import _run

TASKS = [{"title": "甲", "instructions": "调研甲，写 /workspace/work/a.txt"}, {"title": "乙", "instructions": "调研乙"}]


class Routing(LLMClient):
    """Answers by role: the parent follows its script, each child by the title in its brief; children overlap in time."""

    def __init__(self, parent, children):
        self.parent, self.children = list(parent), {k: list(v) for k, v in children.items()}
        self.requests: dict[str, list] = {"parent": []}
        self.inflight = self.peak = 0

    async def chat(self, messages, tools, model, user_id=None):
        brief = str(messages[-1].get("content")) if messages[-1].get("role") == "user" else ""
        who = next((t for t in self.children if f"子任务「{t}」" in json.dumps(messages, ensure_ascii=False)), "parent")
        self.requests.setdefault(who, []).append({"messages": [dict(m) for m in messages], "tools": tools, "brief": brief})
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        await asyncio.sleep(0.01)
        self.inflight -= 1
        return (self.parent if who == "parent" else self.children[who]).pop(0)


def test_children_fork_the_parent_prefix_run_concurrently_and_report_back():
    model = Routing(
        [LLMResponse(tool_calls=[ToolCall("s", "run_subtasks", {"tasks": TASKS})]), LLMResponse(content="汇总完成")],
        {
            "甲": [
                LLMResponse(tool_calls=[ToolCall("w", "write", {"path": "/workspace/work/a.txt", "content": "甲的资料"})]),
                LLMResponse(tool_calls=[ToolCall("d", "deliver_file", {"path": "/workspace/outputs/x.pptx"})]),
                LLMResponse(content="甲：已写 a.txt"),
            ],
            "乙": [LLMResponse(content="乙：结论是 B")],
        },
    )
    platform, broker = _run(model)
    assert platform.statuses[-1][0] == "succeeded"
    parent_first = model.requests["parent"][0]
    for title in ("甲", "乙"):
        child = model.requests[title][0]
        # The child's request is the parent's request plus its brief: same tools, same leading messages.
        assert child["tools"] == parent_first["tools"]
        assert child["messages"][:-1] == parent_first["messages"]
        assert child["messages"][-1]["content"].startswith(subtasks.BRIEF.split("{title}")[0])
    assert model.peak >= 2
    refused = json.loads(model.requests["甲"][2]["messages"][-1]["content"])
    assert refused["ok"] is False and "deliver_file" in refused["error"]
    assert any(path == "/workspace/work/a.txt" for path, _ in broker.put_calls)
    reported = json.loads(next(m["content"] for m in model.requests["parent"][1]["messages"] if m.get("role") == "tool"))
    assert [r["report"] for r in reported["results"]] == ["甲：已写 a.txt", "乙：结论是 B"]
    assert reported["ok"] is True
    statuses = [(p["title"], p["status"]) for t, p in platform.events if t == "agent.subtask"]
    assert ("甲", "done") in statuses and ("乙", "done") in statuses


def test_a_single_task_is_refused_before_any_child_runs():
    model = Routing(
        [LLMResponse(tool_calls=[ToolCall("s", "run_subtasks", {"tasks": TASKS[:1]})]), LLMResponse(content="自己做")], {"甲": []}
    )
    _run(model)
    result = json.loads(next(m["content"] for m in model.requests["parent"][1]["messages"] if m.get("role") == "tool"))
    assert result["ok"] is False and "2–4" in result["error"]
    assert "甲" not in model.requests or not model.requests["甲"]


def test_v2_children_copy_original_and_view_in_parent_lease_without_platform_document_tools(monkeypatch):
    import base64
    import io

    from PIL import Image

    from ailab_platform.catalog import Catalog, ModelSpec
    from tests.test_agent_loop import FakeBroker, FakePlatform

    image = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(image, "JPEG")
    encoded = base64.b64encode(image.getvalue()).decode()
    monkeypatch.setattr(
        "ailab_platform.agent.loop.get_catalog",
        lambda: Catalog([ModelSpec("m", "m", provider="mock", context_tokens=200000, capabilities=frozenset({"tools", "vision"}))]),
    )

    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "options": {"mode": "work", "document_tools_version": 2}}

        async def library(self, run_id, action, arguments):
            assert action == "authorize-copy" and arguments == {"file_id": "original"}
            return {"file_id": "original", "filename": "document.pdf"}

    class Broker(FakeBroker):
        async def copy_library_file(self, lease_id, file_id, path):
            assert lease_id == "lease_1" and file_id == "original"
            return {"file_id": file_id, "path": path}

        async def view_workspace_file(self, lease_id, path, pages):
            assert lease_id == "lease_1" and path == "/workspace/inputs/document.pdf" and pages == [2]
            return {"pages": [2], "images": [encoded]}

    model = Routing(
        [LLMResponse(tool_calls=[ToolCall("children", "run_subtasks", {"tasks": TASKS})]), LLMResponse(content="汇总")],
        {
            "甲": [
                LLMResponse(
                    tool_calls=[
                        ToolCall("copy", "copy_library_file", {"file_id": "original", "path": "/workspace/inputs/document.pdf"}),
                        ToolCall("view", "view_workspace_file", {"path": "/workspace/inputs/document.pdf", "pages": [2]}),
                        ToolCall("forged", "read_library_file", {"file_id": "original"}),
                    ]
                ),
                LLMResponse(content="已查看原件第二页"),
            ],
            "乙": [LLMResponse(content="另一项完成")],
        },
    )
    platform, broker = _run(model, platform=Platform(), broker=Broker())
    assert platform.statuses[-1][0] == "succeeded" and broker.leases == 1
    request = model.requests["甲"][1]
    results = [json.loads(m["content"]) for m in request["messages"] if m.get("role") == "tool"]
    assert results[-3]["ok"] and results[-2]["ok"] and not results[-1]["ok"]
    assert request["messages"][-1]["content"][1]["image_url"]["url"].endswith(encoded)
    assert not any(
        t["function"]["name"] in {"read_library_file", "read_document", "view_document", "extract_text"} for t in request["tools"]
    )
    assert model.requests["parent"][0]["tools"] == request["tools"]
