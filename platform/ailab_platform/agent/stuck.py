"""Semantic repetition detection over durable tool observations, independent of model context."""

import hashlib
import json

VOLATILE = frozenset({"duration_ms", "idle_seconds", "elapsed_seconds", "timestamp", "started_at", "finished_at", "hint"})
STUCK_ERROR = "检测到重复操作，已暂停，可继续"
STUCK_NOTE = "（平台提示：检测到重复操作。请停止重复，改变方法，或向用户询问所需信息。）"


def normalized(value):
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in sorted(value.items()) if key not in VOLATILE}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(normalized(value), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def bookkeeping_result(result):
    kept = {"ok": bool(result.get("ok")), "compacted": True}
    if result.get("interrupted"):
        kept["interrupted"] = True
    if result.get("error"):
        kept["error"] = str(result["error"])[:500]
    return kept


def observation(name, arguments, result):
    """Keep identity and bounded bookkeeping; model messages own the consumed payload."""
    return {
        "name": name,
        "arguments_fingerprint": fingerprint(arguments),
        "result_fingerprint": fingerprint(result),
        "failed": result.get("ok") is False or bool(result.get("error")),
        "waiting": name == "exec_wait" and result.get("running") is True,
        "result": bookkeeping_result(result),
    }


def compact_log(state):
    # Legacy completed records can coexist with pending results not yet paired into messages.
    pending = {f"{state['next_step']}:{item['ordinal']}" for item in state.get("pending", [])}
    for key, record in list(state["tool_log"].items()):
        if key not in pending and "arguments" in record and "result" in record:
            state["tool_log"][key] = observation(record["name"], record["arguments"], record["result"])
        elif key not in pending and "result" in record:
            record["result"] = bookkeeping_result(record["result"])


def detect_loop(tool_log, start=0):
    """Inspect a bounded suffix; running waits break repetition patterns."""
    ordered = sorted(tool_log.items(), key=lambda item: tuple(int(n) for n in item[0].split(":")))
    records = []
    for _, record in ordered[start:][-12:]:
        if "arguments" in record and "result" in record:
            record = observation(record["name"], record["arguments"], record["result"])
        if "arguments_fingerprint" not in record or record.get("waiting"):
            records = []
        else:
            records.append(record)
    calls = [(r["name"], r["arguments_fingerprint"]) for r in records]
    pairs = [(call, r["result_fingerprint"]) for call, r in zip(calls, records)]
    if len(pairs) >= 4 and len(set(pairs[-4:])) == 1:
        return "identical_call_result"
    if len(records) >= 3 and all(r["failed"] for r in records[-3:]) and len(set(calls[-3:])) == 1:
        return "repeated_call_error"
    if len(pairs) == 12 and pairs[-1] != pairs[-2] and pairs == pairs[-2:] * 6:
        return "alternating_call_result"
    return None
