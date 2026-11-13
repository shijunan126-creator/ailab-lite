"""On-demand tools: rarely used tools stay out of the request and are reached through one fixed `call_tool`.

A provider only accepts calls to functions declared in the request, and the declarations sit before every message,
so adding a tool mid-run would discard the whole cached prefix. Instead the request declares the tools most turns use
plus `call_tool`, whose `name` enum and description list the rest. The first `call_tool(name)` returns that tool's
full description and parameters as a tool result, appended like any other, and later calls run it. The request never
changes inside a run, and a plain turn does not pay for the schemas of tools it will not use.

Which tools stay declared is decided from how often runs actually call them; see the prompt-diet design.
"""

from __future__ import annotations

import json

from ..llm import ToolCall

CALL = "call_tool"
DOC_KEY = "tool_doc"

# Called in a small share of runs, or only when the user asks for that specific thing.
DEFERRED = {
    "work": frozenset(
        {
            "ask_user",
            "bash_kill",
            "copy_library_file",
            "download_url",
            "fetch_packages",
            "fetch_repo",
            "memory",
            "ocr_workspace_image",
            "recall_history",
            "resolve_attachment",
            "run_js",
            "run_subtasks",
            "save_skill",
            "schedule",
            "search_images",
            "search_library",
            "update_plan",
        }
    ),
    "chat": frozenset({"memory", "recall_history", "resolve_attachment", "run_js", "save_skill", "schedule", "search_library"}),
}

# What the model needs to pick a tool before reading its description.
SUMMARY = {
    "ask_user": "缺少关键信息时向用户提问并等待回答",
    "bash_kill": "停止后台命令",
    "copy_library_file": "把资料库文件复制到工作区",
    "download_url": "下载网上的文件到工作区",
    "fetch_packages": "安装 PyPI 依赖（沙箱不能联网）",
    "fetch_repo": "获取公开代码仓库",
    "memory": "保存、修改、删除或列出用户的长期记忆",
    "ocr_workspace_image": "识别工作区图片里的文字",
    "recall_history": "搜索和阅读用户过去的对话原文",
    "resolve_attachment": "定位当前或历史消息里的附件",
    "run_js": "在用户浏览器里做精确计算或数据转换",
    "run_subtasks": "用户明确要求时把任务拆成并行子任务",
    "save_skill": "把可复用的方法保存为个人技能",
    "schedule": "创建和管理定时任务、提醒",
    "search_images": "搜索网络图片素材",
    "search_library": "按文件名搜索用户的资料库",
    "update_plan": "记录多步任务的计划和进度",
}


def _name(tool: dict) -> str:
    return tool["function"]["name"]


def call_tool(deferred: list[dict]) -> dict:
    names = [_name(t) for t in deferred]
    lines = "\n".join(f"- {name}：{SUMMARY.get(name, '')}" for name in names)
    return {
        "type": "function",
        "function": {
            "name": CALL,
            "description": "调用下列按需工具。第一次用某个工具时只传 name，读取它的说明和参数；之后传 name 和 arguments 调用。\n" + lines,
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "enum": names}, "arguments": {"type": "object"}},
                "required": ["name"],
            },
        },
    }


def split(catalog: list[dict], mode: str) -> list[dict]:
    """The tools a request declares: the frequent ones in catalog order, then `call_tool` for the rest."""
    wanted = DEFERRED.get(mode, DEFERRED["work"])
    deferred = [t for t in catalog if _name(t) in wanted]
    if not deferred:
        return catalog
    return [*(t for t in catalog if _name(t) not in wanted), call_tool(deferred)]


def names(offered: list[dict]) -> frozenset[str]:
    """The on-demand tools a frozen request offers, read back from its `call_tool` declaration."""
    for tool in offered or []:
        if _name(tool) == CALL:
            return frozenset(tool["function"]["parameters"]["properties"]["name"].get("enum") or [])
    return frozenset()


def _documented(messages: list[dict], name: str) -> bool:
    marker = json.dumps({DOC_KEY: name}, ensure_ascii=False)[1:-1]
    return any(m.get("role") == "tool" and marker in str(m.get("content", "")) for m in messages)


def _arguments(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def resolve(call: ToolCall, available: frozenset[str], messages: list[dict]) -> ToolCall | None:
    """The real call behind a `call_tool` that can run now, else None (it gets `describe` instead).

    A tool runs only once its description is in the context: a schedule prompt or a memory line written without the
    tool's rules is worse than one extra round trip.
    """
    args = call.arguments or {}
    name, inner = args.get("name"), _arguments(args.get("arguments"))
    if name not in available or inner is None or not _documented(messages, name):
        return None
    return ToolCall(call.id, name, inner)


def describe(arguments: dict, definitions: dict[str, dict], messages: list[dict]) -> dict:
    """The result of a `call_tool` that did not run: the tool's description, or why the call cannot go ahead."""
    name = (arguments or {}).get("name")
    tool = definitions.get(name)
    if tool is None:
        return {"ok": False, "error": f"没有按需工具 {name!r}；可用：{', '.join(sorted(definitions))}"}
    if _documented(messages, name):
        if "arguments" in (arguments or {}):
            return {"ok": False, "error": "arguments 必须是 JSON 对象"}
        return {"ok": True, "note": f"{name} 的说明已在上文，直接传 arguments 调用"}
    function = tool["function"]
    return {
        "ok": True,
        DOC_KEY: name,
        "description": function.get("description", ""),
        "parameters": function.get("parameters", {}),
        "next": f"用 call_tool(name={name!r}, arguments={{...}}) 调用",
    }
