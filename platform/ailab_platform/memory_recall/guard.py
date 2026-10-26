"""Deterministic checks a candidate memory must pass before it is stored.

These run before any model-assisted validation (architecture doc §8.5). They are deliberately
conservative: a rejected fact costs one sentence the user can re-add by hand, while a stored
credential or a persisted instruction is a standing hazard.
"""

from __future__ import annotations

import re
import unicodedata

SECRET = "secret"
INJECTION = "injection"

_SECRET_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"\bsk-[a-z0-9_\-]{16,}",  # OpenAI / DeepSeek style API keys
        r"\bgh[pousr]_[a-z0-9]{20,}",  # GitHub tokens
        r"\bAKIA[0-9A-Z]{16}\b",  # AWS access key id
        r"\bAIza[0-9A-Za-z_\-]{30,}",  # Google API key
        r"\bxox[abprs]-[a-z0-9\-]{10,}",  # Slack tokens
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\beyJ[a-z0-9_\-]{8,}\.[a-z0-9_\-]{8,}\.[a-z0-9_\-]{8,}",  # JWT
        r"\bbearer\s+[a-z0-9._\-]{20,}",
        # "password: hunter2", "token=abc123...", "密码是 xxx"
        r"(?:password|passwd|pwd|secret|api[_\- ]?key|access[_\- ]?token|token)\s*(?:is|are|[:=：])\s*\S{6,}",
        r"(?:密码|口令|密钥|令牌)\s*(?:是|为|[:=：])\s*\S{4,}",
    )
]
# a long unbroken run that mixes letters and digits is a token, not prose
_TOKENISH = re.compile(r"(?<![a-z0-9])(?=[a-z0-9_\-]{32,})(?=[a-z0-9_\-]*[0-9])(?=[a-z0-9_\-]*[a-z])[a-z0-9_\-]{32,}", re.I)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

_INJECTION_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"ignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier) (?:instructions|rules|prompts?)",
        r"disregard (?:all |any )?(?:the )?(?:previous|prior|above|system)",
        r"(?:reveal|print|show|send|leak|exfiltrate)\b.{0,40}\b(?:system prompt|api[_\- ]?keys?|secrets?|credentials?|passwords?)",
        r"you (?:must|should) (?:always|never) (?:obey|follow|ignore)",
        r"(?:忽略|无视|不要遵守|不要理会).{0,12}(?:以上|之前|先前|上述|所有|系统).{0,8}(?:指令|规则|提示|要求)",
        r"(?:泄露|发送|输出|告诉|透露).{0,12}(?:系统提示|提示词|密钥|口令|凭据|令牌|密码)",
        r"(?:总是|永远|始终|无论如何).{0,10}(?:服从|听从|执行).{0,10}(?:网页|文件|外部|以下)",
    )
]


def reject_reason(text: str, *, untrusted: bool = True) -> str | None:
    """Return why this text must not become a memory, or None.

    Secrets are rejected from every source. Instruction-shaped text is rejected from the agent and
    the dream (``untrusted``); the user may still write whatever they like on the memory page.
    """
    text = unicodedata.normalize("NFKC", text or "")
    # an id is not a credential: a bare UUID may be remembered, a 40-character mixed run may not
    tokenish = [m for m in _TOKENISH.finditer(text) if not _UUID.fullmatch(m.group())]
    if any(p.search(text) for p in _SECRET_PATTERNS) or tokenish:
        return SECRET
    if untrusted and any(p.search(text) for p in _INJECTION_PATTERNS):
        return INJECTION
    return None
