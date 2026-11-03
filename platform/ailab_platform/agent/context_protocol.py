"""Closed model/tool protocol units; no execution or context ownership."""


def protocol_groups(messages):
    """Return half-open ranges, keeping each assistant tool batch indivisible."""
    groups = []
    start = 0
    pending = set()
    seen = set()
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in pending:
                raise ValueError("orphan tool result at compaction barrier")
            pending.remove(call_id)
            if not pending:
                groups.append((start, index + 1))
        else:
            if pending:
                raise ValueError("dangling tool call at compaction barrier")
            start = index
            calls = message.get("tool_calls") or []
            for call in calls:
                call_id = call.get("id")
                if role != "assistant" or not call_id or call_id in seen:
                    raise ValueError("invalid tool call at compaction barrier")
                pending.add(call_id)
                seen.add(call_id)
            if not pending:
                groups.append((index, index + 1))
    if pending:
        raise ValueError("dangling tool call at compaction barrier")
    return groups


def validate_safe_compaction_point(messages, runtime_state):
    # A closed exec pair may still have a live process. Its handle is operational
    # state, not an unresolved model/tool protocol pair.
    if runtime_state.get("pending") or runtime_state.get("model_in_flight"):
        raise ValueError("unresolved work at compaction barrier")
    return protocol_groups(messages)
