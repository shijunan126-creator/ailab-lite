"""Deterministic boundary blocks; inherited wire messages never pass here."""

import json

from ..contracts.context_summary import ContextSummary, canonical_summary, format_instructions, validate_summary
from .skill_modules import SUMMARY_MARKER, module_summary


def source_handles(value=None, *, run_ids=(), attachments=(), evidence=(), summary=None):
    """Authority comes only from API-owned metadata, never model-visible text.

    `value` is deliberately ignored for legacy callers: messages and arbitrary
    JSON cannot grant provenance. Callers must select a typed metadata channel.
    """
    allowed = {key: set() for key in ("run_id", "file_id", "block_id")}
    for run_id in run_ids:
        if isinstance(run_id, str) and run_id:
            allowed["run_id"].add(run_id)
    for attachment in attachments:
        file_id = attachment.get("file_id") or attachment.get("id")
        if isinstance(file_id, str) and file_id:
            allowed["file_id"].add(file_id)
    for record in evidence:
        for key in allowed:
            if isinstance(record.get(key), str):
                allowed[key].add(record[key])
    if summary:
        try:
            parsed = ContextSummary.model_validate_json(summary)
        except ValueError:
            pass
        else:
            for source in parsed.sources():
                for key, handle in source.model_dump(exclude_none=True).items():
                    if key in allowed:
                        allowed[key].add(handle)
    return allowed


def summary_instruction(text, allowed):
    if sum(map(len, allowed.values())) > 2048:
        raise ValueError("summary source handles exceed boundary budget")
    handles = {key: sorted(value) for key, value in allowed.items()}
    return (
        text
        + format_instructions()
        + "只可引用以下来源句柄；没有来源的判断放入uncertainties："
        + json.dumps(handles, ensure_ascii=False, separators=(",", ":"))
    )


def parse_summary(response, allowed):
    if response.tool_calls:
        raise ValueError("summary returned tool calls")
    summary = validate_summary(response.content, allowed)
    summary.read_modules = []  # navigation is exclusively derived from program-owned evidence
    return summary


def semantic_message(summary):
    return {
        "role": "system",
        "content": "<conversation_checkpoint>\n" + canonical_summary(summary, modules=False) + "\n</conversation_checkpoint>",
    }


def stored_summary_messages(text):
    if not text:
        return []
    try:
        summary = ContextSummary.model_validate_json(text)
    except ValueError:
        # Old durable summaries remain readable; only new boundaries require v1.
        return [{"role": "system", "content": "历史摘要（仅作背景）：\n" + text}]
    modules = [entry.model_dump() for entry in summary.read_modules]
    return [semantic_message(summary), block("loaded_modules", modules)]


def block(name, value):
    return {
        "role": "system",
        "content": f"<{name}>\n" + json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + f"\n</{name}>",
    }


def loaded_modules(messages):
    # Accept old navigation markers only at a boundary; never change old bytes.
    note = module_summary(messages)
    entries = set(tuple(item) for item in json.loads(note.split(SUMMARY_MARKER)[1])) if note else set()
    for message in messages:
        text = message.get("content")
        if message.get("role") == "system" and isinstance(text, str) and text.startswith("<loaded_modules>\n"):
            value = json.loads(text.split("\n")[1])
            entries.update(
                tuple(item[key] for key in ("name", "resource", "hash"))
                for item in (value.get("items", []) if isinstance(value, dict) else value)
            )
    return [dict(zip(("name", "resource", "hash"), item, strict=True)) for item in sorted(entries)]


def operational_context_message(state):
    # No credentials, leases, tool output or storage URLs belong in model context.
    workspace = state.get("workspace") or {}
    return block(
        "runtime_state",
        {
            "background_jobs": sorted(state.get("background_jobs") or []),
            "confirmed_deliveries": [
                {"path": path, "name": (state.get("delivery_names") or {}).get(path, path.rsplit("/", 1)[-1])}
                for path in sorted(state.get("delivered") or {})
            ],
            "plan": state.get("plan") or [],
            "workspace": {"available": bool(workspace), "dirty": bool(state.get("workspace_dirty"))},
            "pending_file_dependencies": state.get("file_wait_deadlines") or {},
        },
    )


def boundary_messages(summary, messages, state):
    return [semantic_message(summary), operational_context_message(state), block("loaded_modules", loaded_modules(messages))]
