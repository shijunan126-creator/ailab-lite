"""Keep the durable recovery envelope within the API's uncompressed byte budget."""

import json

from ..adapters.runtime import fresh_state

CAPACITY_NOTE = "执行记录已达保存容量，已暂停；请检查已交付文件并在新任务中继续，避免重复执行命令"


def encoded_size(state):
    return len(json.dumps(state, ensure_ascii=False).encode())


def bounded_checkpoint(state, limit):
    if encoded_size(state) <= limit:
        return state, False
    # Once full, freeze execution. Never discard a paid pending response and then
    # let recovery replay it, or pretend an omitted command is safe to start again.
    frozen = fresh_state()
    frozen.update(
        checkpoint_capacity_exceeded=True,
        workspace=state.get("workspace"),
        workspace_dirty=state.get("workspace_dirty", True),
        next_step=state["next_step"],
        budget=state.get("budget", {}),
        final_summary=CAPACITY_NOTE,
        tool_log={
            key: {"name": record["name"], "started": True} for key, record in state.get("tool_log", {}).items() if "result" not in record
        },
    )
    # These records are useful for inspection, but no frozen checkpoint executes.
    for key in ("delivered", "artifacts", "sources", "plan", "tool_log"):
        candidate = {**frozen, key: state.get(key, frozen[key])}
        if encoded_size(candidate) <= limit:
            frozen = candidate
    if encoded_size(frozen) > limit:
        raise ValueError("checkpoint budget cannot fit the recovery envelope")
    return frozen, True
