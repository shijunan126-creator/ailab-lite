"""Local progressive disclosure; discovery state is checkpointed separately from prose."""

import re

from ..internal_skills import metadata

SEARCH = {
    "type": "function",
    "function": {
        "name": "search_capabilities",
        "description": "按需发现平台工具或内部办公技能；关键词如PDF、表格、网页、执行。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 200},
                "kind": {"type": "string", "enum": ["tool", "skill", "any"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}
CORE = {"search_capabilities", "load_skill", "recall_history", "ask_user", "update_plan"}
ALIASES = {
    "执行": "exec",
    "计算": "exec",
    "代码": "exec",
    "网页": "web",
    "搜索": "search",
    "文件": "library",
    "读取": "read",
    "图片": "image",
    "办公": "platform",
    "PDF": "pdf",
    "表格": "spreadsheet",
}


def offered(catalog, active):
    return [SEARCH, *[t for t in catalog if t["function"]["name"] in CORE | set(active)]]


def search(query, kind, limit, catalog, user_skills=()):
    query = str(query)[:200]
    terms = [*re.findall(r"[\w.]+", query.lower()), *[v for k, v in ALIASES.items() if k in query]]
    entries = []
    if kind in {"any", "tool"}:
        entries += [
            {"kind": "tool", "name": t["function"]["name"], "description": t["function"].get("description", "")[:160], "schema": t}
            for t in catalog
        ]
    if kind in {"any", "skill"}:
        entries += [{"kind": "skill", **m} for m in [*metadata(), *user_skills]]

    def score(entry):
        text = (entry["name"] + " " + entry["description"]).lower()
        return sum(1 for term in terms if term in text)

    return sorted([e for e in entries if score(e)], key=lambda e: (-score(e), e["name"]))[: max(1, min(int(limit), 5))]


def activate(state, names, catalog):
    allowed = {t["function"]["name"] for t in catalog}
    active = list(state.get("active_tools") or [])
    for name in names:
        if name in allowed and name not in CORE:
            if name in active:
                active.remove(name)
            active.append(name)
    state["active_tools"] = active[-8:]
    state["capability_catalog_revision"] = 1


def bounded(catalog, active, budget_tokens):
    from ..model_policy import estimator

    selected = list(active)
    while selected and estimator.raw(offered(catalog, selected)) > budget_tokens:
        selected.pop(0)
    result = offered(catalog, selected)
    if estimator.raw(result) > budget_tokens:
        raise ValueError("capability entry schemas exceed model budget")
    return result
