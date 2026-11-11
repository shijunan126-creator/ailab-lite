"""Skill discovery and versioned model tool catalogs."""

import re

SEARCH = {
    "type": "function",
    "function": {
        "name": "search_capabilities",
        "description": "按关键词发现可用技能。",
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
# Version 1 is intentionally fixed: selection does not depend on prompt, user, or prior tool calls.
REDUCED_V1 = frozenset(
    {
        "document_view",
        "search_capabilities",
        "load_skill",
        "memory",
        "recall_history",
        "ask_user",
        "update_plan",
        "bash",
        "bash_wait",
        "deliver_file",
        "review_artifact",
    }
)


REDUCED_V2 = (REDUCED_V1 - {"document_view"}) | frozenset(
    {
        "read_document",
        "search_document",
        "view_document",
        "resolve_attachment",
        "search_library",
        "copy_library_file",
        "view_workspace_file",
        "ocr_workspace_image",
    }
)


def catalog_for_budget(catalog, budget_tokens, version=1):
    from ..model_policy import estimator

    if estimator.raw(catalog) <= budget_tokens:
        return catalog
    names = REDUCED_V2 if version == 2 else REDUCED_V1
    reduced = [tool for tool in catalog if tool["function"]["name"] in names]
    if estimator.raw(reduced) > budget_tokens:
        raise ValueError(f"version {version} reduced tool catalog exceeds model schema budget")
    return reduced


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


def search(query, limit, user_skills=()):
    query = str(query)[:200]
    terms = [*re.findall(r"[\w.]+", query.lower()), *[v for k, v in ALIASES.items() if k.lower() in query.lower()]]
    entries = [{"kind": "skill", **m} for m in user_skills]

    def score(entry):
        text = (entry["name"] + " " + entry["description"]).lower()
        return sum(1 for term in terms if term in text)

    return sorted([e for e in entries if score(e)], key=lambda e: (-score(e), e["name"]))[: max(1, min(int(limit), 5))]
