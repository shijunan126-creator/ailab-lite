"""Skill discovery and the one model tool catalog per mode."""

import re

SEARCH = {
    "type": "function",
    "function": {
        "name": "search_capabilities",
        "description": "按关键词查找可用的技能和按需工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 200},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

ALIASES = {
    "执行": "bash",
    "计算": "run_js",
    "json": "run_js",
    "正则": "run_js",
    "csv": "run_js",
    "js": "run_js",
    "代码": "bash",
    "网页": "web",
    "搜索": "search",
    "文件": "library",
    "读取": "read",
    "图片": "image",
    "办公": "platform",
    "PDF": "pdf",
    "表格": "spreadsheet",
}


def search(query, limit, user_skills=(), tools=()):
    """Skills, and on-demand tools (`call_tool`) by their one-line summaries."""
    query = str(query)[:200]
    terms = [*re.findall(r"[\w.]+", query.lower()), *[v for k, v in ALIASES.items() if k.lower() in query.lower()]]
    entries = [{"kind": "skill", **m} for m in user_skills] + [{"kind": "tool", **t, "use": "call_tool"} for t in tools]

    def score(entry):
        text = (entry["name"] + " " + entry["description"]).lower()
        return sum(1 for term in terms if term in text)

    return sorted([e for e in entries if score(e)], key=lambda e: (-score(e), e["name"]))[: max(1, min(int(limit), 5))]
