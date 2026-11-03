"""ProcessView V1, a product projection; never model context or recovery state.

Live SSE has an incremental Portal reducer. A shared fixture locks its display
semantics to this projection, including the final-answer/narration boundary.
"""

import re

PROCESS_SNAPSHOT_SCHEMA_VERSION = 1
PROCESS_EVENT_TYPES = frozenset(
    {
        "agent.thinking",
        "agent.delta",
        "agent.message",
        "agent.tool_call",
        "agent.tool_result",
        "plan.updated",
        "agent.ask_user",
        "agent.ask_answered",
        "agent.steered",
        "run.resumed",
        "agent.model_fallback",
        "agent.context_compacted",
        "agent.budget",
        "agent.skill",
        "agent.attachments",
        "agent.waiting_sandbox",
        "preview.updated",
        "preview.failed",
        "agent.warning",
        "agent.autodeliver",
    }
)
SKILLS = {
    "platform.pdf": "PDF",
    "platform.docx": "Word",
    "platform.xlsx": "Excel",
    "platform.pptx": "PPT",
    "pptx": "PPT 设计",
    "xlsx": "Excel",
    "auto-skill": "提取技能",
}
LABELS = {
    "load_skill": "读取技能",
    "search_capabilities": "查找可用能力",
    "run_js": "运行 JavaScript",
    "search_images": "搜索图片",
    "exec": "运行命令",
    "exec_wait": "查看命令进度",
    "exec_kill": "停止命令",
    "write_file": "写入文件",
    "read_file": "读取文件",
    "list_files": "查看目录",
    "deliver_file": "交付文件",
    "web_search": "搜索网页",
    "read_url": "读取网页",
    "download_url": "下载文件",
    "fetch_repo": "获取仓库",
    "fetch_packages": "安装依赖",
    "search_library": "搜索资料库",
    "read_library_file": "阅读资料",
    "copy_library_file": "复制到工作区",
    "memory": "更新记忆",
    "recall_history": "回忆历史对话",
    "update_plan": "更新计划",
}
REASONS = {
    "identical_call_result": "同一操作重复得到相同结果",
    "repeated_call_error": "同一操作反复出错",
    "alternating_call_result": "两个操作来回重复",
}


def describe_tool(name, args):
    a = args or {}
    label = LABELS.get(name, name)
    detail = None
    if name == "load_skill":
        detail = SKILLS.get(a.get("name"), a.get("name"))
    elif name in {"search_capabilities", "search_images", "web_search", "search_library", "recall_history"}:
        detail = a.get("query")
    elif name == "run_js":
        detail = next((line.strip()[:120] for line in str(a.get("code") or "").splitlines() if line.strip()), None)
    elif name == "exec":
        detail = str(a.get("cmd") or a.get("command") or "")[:160]
    elif name in {"write_file", "read_file", "list_files"}:
        detail = a.get("path")
    elif name == "deliver_file":
        detail = a.get("filename") or str(a.get("path") or "").split("/")[-1]
    elif name in {"read_url", "download_url"}:
        detail = a.get("url")
    elif name == "fetch_repo":
        detail = "@".join(filter(None, [a.get("repo"), a.get("ref")]))
    elif name == "fetch_packages":
        detail = " ".join(a.get("packages") or [])
    elif name == "save_skill":
        label = "更新个人技能" if a.get("mode") == "update" else "保存个人技能"
        detail = a.get("name")
    elif name == "read_library_file":
        detail = a.get("filename") or a.get("file_id")
    elif name == "copy_library_file":
        detail = a.get("path") or a.get("filename") or a.get("file_id")
    return {"label": label, **({"detail": detail} if detail is not None else {})}


def push_step(items, step):
    if items and items[-1]["kind"] == "steps":
        items[-1]["steps"].append(step)
    else:
        items.append({"kind": "steps", "steps": [step]})


def reduce_event(items, type_, p):
    tail = items[-1] if items else None
    if type_ == "agent.thinking" and tail and tail["kind"] == "thinking" and tail.get("step") == p.get("step") and not tail["done"]:
        tail["text"] += p.get("text") or ""
        tail["seconds"] = p.get("seconds", tail["seconds"])
        return
    if type_ == "agent.delta" and tail and tail["kind"] == "text" and tail.get("step") == p.get("step"):
        tail["text"] += p.get("text") or ""
        return
    if type_ != "agent.thinking" and tail and tail["kind"] == "thinking":
        tail["done"] = True

    def note(label, detail=None, status="note"):
        push_step(items, {"name": type_, "label": label, "status": status, **({"detail": detail} if detail is not None else {})})

    if type_ == "agent.thinking":
        items.append(
            {"kind": "thinking", "step": p.get("step"), "text": p.get("text") or "", "seconds": p.get("seconds") or 0, "done": False}
        )
    elif type_ in {"agent.delta", "agent.message"}:
        text = p.get("content" if type_ == "agent.message" else "text") or ""
        old = (
            next((i for i in reversed(items) if i["kind"] == "text" and i.get("step") == p.get("step")), None)
            if type_ == "agent.message"
            else None
        )
        if old is not None:
            old["text"] = text
        else:
            items.append({"kind": "text", "step": p.get("step"), "text": text})
    elif type_ == "agent.tool_call":
        push_step(
            items,
            {
                "name": p["name"],
                **describe_tool(p["name"], p.get("arguments")),
                "status": "running",
                **({"modelStep": p["step"]} if "step" in p else {}),
            },
        )
    elif type_ == "agent.tool_result":
        for item in reversed(items):
            if item["kind"] != "steps":
                continue
            for step in reversed(item["steps"]):
                if step["name"] != p.get("name") or step["status"] != "running" or ("step" in p and step.get("modelStep") != p["step"]):
                    continue
                step.update(
                    status="ok" if p.get("ok") else "failed",
                    output="\n".join(str(p[k]) for k in ("error", "stdout", "stderr") if p.get(k))[:1500],
                )
                if p.get("running"):
                    step.update(label=step["label"] + " · 仍在运行", status="note")
                if isinstance(p.get("sources"), list):
                    step["sources"] = [s for s in p["sources"] if s and re.match(r"^https?://", s.get("url") or "")][:10]
                return
    elif type_ == "plan.updated":
        before = {s["id"]: s for i in items if i["kind"] == "plan" for s in i["steps"]}
        changed = [
            s.copy()
            for s in p.get("steps", [])
            if s["id"] not in before or any(before[s["id"]].get(k) != s.get(k) for k in ("title", "status"))
        ]
        if changed:
            items.append({"kind": "plan", "steps": changed})
    elif type_ == "agent.ask_user":
        if not any(i["kind"] == "ask" and i["id"] == p.get("question_id") for i in items):
            items.append(
                {
                    "kind": "ask",
                    "id": p.get("question_id"),
                    "question": p.get("question") or "",
                    "options": p.get("options") or [],
                    "allowText": p.get("allow_text") is not False,
                }
            )
    elif type_ == "agent.ask_answered":
        for i in items:
            if i["kind"] == "ask" and i["id"] == p.get("question_id"):
                i["answer"] = p.get("answer") or ""
    elif type_ == "agent.steered":
        if not any(i["kind"] == "steer" and i["id"] == p.get("queued_id") for i in items):
            items.append({"kind": "steer", "id": str(p.get("queued_id") or ""), "prompt": str(p.get("prompt") or "")})
    elif type_ == "run.resumed":
        note("从中断处继续", status="ok")
    elif type_ == "agent.model_fallback":
        note("已换用备用模型", " → ".join(filter(None, [p.get("from_label") or p.get("from"), p.get("to_label") or p.get("to")])) or None)
    elif type_ == "agent.context_compacted":
        key = "messages" if p.get("scope") == "run" else "turns"
        note(
            "已压缩本轮较早的步骤" if key == "messages" else "已压缩较早的对话",
            f"{p[key]} {'条' if key == 'messages' else '轮'}" if p.get(key) else None,
        )
    elif type_ == "agent.budget":
        minutes = f"已工作 {int(p['minutes'] + 0.5)} 分钟" if isinstance(p.get("minutes"), (int, float)) else None
        kind = p.get("kind")
        if kind == "nudge":
            note("提醒换个方法", REASONS.get(p.get("reason"), p.get("reason")))
        elif kind == "wrap_up":
            note("开始收尾", p.get("reason") or minutes)
        elif kind == "soft":
            note("检查进度", minutes)
        elif kind == "extended":
            note("有进展，继续工作", f"延长 {p['extension_minutes']} 分钟" if p.get("extension_minutes") else None, "ok")
        elif kind == "paused":
            note("已暂停", re.sub(r"[，,]?\s*已暂停.*$", "", str(p.get("reason") or "")) or minutes)
    elif type_ == "agent.skill":
        detail = SKILLS.get(p.get("name"), p.get("name"))
        for i in reversed(items):
            if i["kind"] == "steps":
                for s in reversed(i["steps"]):
                    if s["name"] == "load_skill" and s.get("detail") == detail:
                        if p.get("title") and p["title"] != detail:
                            s["detail"] = p["title"]
                        return
        if p.get("status") == "failed":
            note("技能未能加载", "：".join(filter(None, [p.get("title") or detail, p.get("error")])), "failed")
        else:
            note("已加载技能", p.get("title") or detail, "ok")
    elif type_ == "agent.attachments":
        for f in p.get("files", []):
            note("已读取附件", f"{f['filename']}（{f['chars']} 字{'，内容较长，只读了开头部分' if f.get('truncated') else ''}）", "ok")
    elif type_ == "agent.waiting_sandbox":
        note("排队中，马上开始…")
    elif type_ == "preview.updated":
        note("预览已更新", p.get("filename"), "ok")
    elif type_ == "preview.failed":
        note("预览生成失败", "：".join(filter(None, [p.get("filename"), p.get("error")])) or None, "failed")
    elif type_ == "agent.warning":
        note("提示", p.get("message"), "failed")
    elif type_ == "agent.autodeliver":
        note("已存入资料库", p.get("filename"))


def process_items(items):
    """Match splitTurn: trailing answer text is not public process narration."""
    cut = len(items)
    while cut:
        item = items[cut - 1]
        if item["kind"] != "text" and (item["kind"] != "steps" or any("modelStep" in s for s in item["steps"])):
            break
        cut -= 1
    return items[:cut] + [i for i in items[cut:] if i["kind"] != "text"]
