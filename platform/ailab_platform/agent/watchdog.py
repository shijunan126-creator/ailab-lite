"""A progress check outside the working loop: is the run still advancing the user's goal?

Repetition detection (stuck.py) catches identical calls; it cannot see a run that keeps doing new,
plausible-looking things in the wrong direction. Every few minutes of active work the same model is
asked to judge that, replaying the run's own messages and tools so the check reads the provider's
prefix cache and only pays for one short instruction and answer.
"""

import json
import re

VERDICTS = ("progressing", "stalled", "drifting")
PROMPT = (
    "（平台监督检查，不是用户消息，不要回复用户。）请只根据上文判断：最近的工作是否在实质推进用户的目标，"
    "还是停滞（反复尝试没有新结果）或偏离（在做用户没要的事）。不要调用工具，只输出一行 JSON："
    '{"verdict":"progressing|stalled|drifting","reason":"不超过60字"}'
)
NUDGE = "（平台监督提示：{reason}。请对照用户的原始要求检查方向和已完成的步骤，换一种做法，或用 ask_user 向用户确认。）"
PAUSE = "监督检查发现任务连续没有实质进展，已暂停，可继续"


def verdict(content: str | None) -> dict:
    """The judged verdict, or "unknown" when the answer is not the one-line JSON asked for."""
    match = re.search(r"\{.*\}", content or "", re.S)
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        data = {}
    value = data.get("verdict") if isinstance(data, dict) else None
    if value not in VERDICTS:
        return {"verdict": "unknown", "reason": ""}
    return {"verdict": value, "reason": str(data.get("reason") or "")[:120]}
