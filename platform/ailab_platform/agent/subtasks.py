"""Parallel subtasks inside one Work run (Super-agent's Worker layer, scaled to a single run).

Each subtask forks the parent's request: the same messages and the same tool schemas, plus one
instruction. The provider therefore serves the shared part from its prefix cache, and the child
starts with everything the parent knows. Tools a child may not use stay in the schema list (removing
them would change the prefix) and are refused when called. Children work in the run's sandbox, return
a short report, and never talk to the user: the parent assembles and delivers.
"""

TOOL = {
    "type": "function",
    "function": {
        "name": "run_subtasks",
        "description": "并行执行2–4个互相独立的子任务（如分别调研几个主题、分别处理几个文件）。每个子任务从当前上下文出发，"
        "可用沙箱、资料和网页工具，但不能交付文件、提问或再派子任务；写清目标、输入和要写入的 /workspace 路径，结果由你汇总。",
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {"title": {"type": "string", "maxLength": 80}, "instructions": {"type": "string", "maxLength": 4000}},
                        "required": ["title", "instructions"],
                    },
                }
            },
            "required": ["tasks"],
        },
    },
}

ALLOWED = frozenset(
    {
        "list_files",
        "read_file",
        "write_file",
        "exec",
        "exec_wait",
        "exec_kill",
        "search_library",
        "read_library_file",
        "web_search",
        "read_url",
        "search_images",
        "recall_history",
    }
)
REPORT_CHARS = 4000

BRIEF = (
    "（平台：你现在是子任务「{title}」的执行者，与其他子任务并行。只完成下面这一项，不要做其他子任务的部分；"
    "不能交付文件、向用户提问或再派子任务。中间文件写到说明给出的 /workspace 路径。完成后用简短文字报告结论、"
    "写了哪些文件和未解决的问题，这段报告会交给主任务汇总。）\n\n{instructions}"
)
REFUSED = "子任务不能使用 {name}；只能用沙箱、资料和网页工具，结论写进报告由主任务处理。"
OUT_OF_STEPS = "子任务步数用完，以下是最后的进展。"


def validate(arguments: dict) -> list[dict]:
    tasks = arguments.get("tasks")
    if not isinstance(tasks, list) or not 2 <= len(tasks) <= 4:
        raise ValueError("tasks 须是 2–4 个子任务")
    out = []
    for task in tasks:
        if not isinstance(task, dict) or not str(task.get("title") or "").strip() or not str(task.get("instructions") or "").strip():
            raise ValueError("每个子任务都需要 title 和 instructions")
        out.append({"title": str(task["title"]).strip()[:80], "instructions": str(task["instructions"]).strip()[:4000]})
    return out
