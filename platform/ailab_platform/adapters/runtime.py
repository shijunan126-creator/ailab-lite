"""Self-developed runtime state, independent of model and orchestration frameworks."""

from copy import deepcopy

RUNTIME_VERSION = 1
NON_REPLAYABLE = frozenset({"exec"})
UNCERTAIN_EXEC = "此命令在中断前可能已部分执行，未重跑；请先检查工作区。"


def fresh_state():
    return {
        "runtime_version": RUNTIME_VERSION,
        "messages": [],
        "next_step": 0,
        "pending": [],
        "tool_log": {},
        "plan": [],
        "artifacts": [],
        "delivered": {},
        "workspace": None,
        "workspace_dirty": False,  # file tools ran since the last saved snapshot
        "final_summary": "",
        "finished": False,
    }


def restored_state(checkpoint):
    state = deepcopy(checkpoint["state"])
    if state.get("runtime_version") != RUNTIME_VERSION:
        raise ValueError("unsupported runtime checkpoint")
    return state


def checkpoint_state(state, image_files):
    """Persist authorized immutable file references instead of copying base64 images at every step."""
    messages = []
    for message in state["messages"]:
        content = message.get("content")
        if isinstance(content, list):
            content = [
                {"type": "ailab_image", "file": image_files[part["image_url"]["url"]]}
                if part.get("type") == "image_url" and part.get("image_url", {}).get("url") in image_files
                else part
                for part in content
            ]
            message = {**message, "content": content}
        messages.append(message)
    return {**state, "messages": messages}
