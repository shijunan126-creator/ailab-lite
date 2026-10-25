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


def detect_loop(tool_log, start=0):
    """Return a reason for a repeated suffix since the latest user/steer boundary.

    Six A/B cycles means twelve observations. Only the last twelve are needed;
    arguments must be durable, so old v1 records without them are ignored.
    """
    ordered = sorted(tool_log.items(), key=lambda item: tuple(int(n) for n in item[0].split(":")))
    records = [record for _, record in ordered[start:][-12:] if "arguments" in record and "result" in record]
    pairs = [fingerprint([r["name"], r["arguments"], r["result"]]) for r in records]
    if len(pairs) >= 4 and len(set(pairs[-4:])) == 1:
        return "identical_call_result"
    if len(records) >= 3:
        errors = records[-3:]
        if all(r["result"].get("ok") is False or r["result"].get("error") for r in errors):
            calls = [fingerprint([r["name"], r["arguments"]]) for r in errors]
            if len(set(calls)) == 1:
                return "repeated_call_error"
    if len(pairs) == 12 and pairs[-1] != pairs[-2] and pairs == pairs[-2:] * 6:
        return "alternating_call_result"
    return None
