"""Where a model's thinking arrives, and how it is separated from the answer.

Most upstreams send thinking in its own delta field (`reasoning_content`, vLLM's newer `reasoning`, Ollama's `thinking`,
OpenRouter's `reasoning_details`). A server without a reasoning parser (vLLM/SGLang started without
`--reasoning-parser`, or a gateway that folds the field back into the text) sends it inside the answer text as
`<think>...</think>`; templates such as DeepSeek-R1's already open the block in the prompt, so only `</think>` arrives.
A model opts into tag splitting in models.toml (`think_tag`, `think_prefilled`): it is never guessed, because an
answer may legitimately contain the string "<think>".

The splitter runs once per streamed chunk of every concurrent model call, so it does constant work per chunk beyond one
`find` over the chunk: it never rescans what it has already emitted and holds back at most one tag's length.
"""

from __future__ import annotations

import re

TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
FIELDS = ("reasoning_content", "reasoning", "thinking")


def reasoning_of(message: dict) -> str:
    """The thinking in one delta or message, whichever field the upstream uses. Only one source is read, so an upstream
    that sends both `reasoning` and its structured `reasoning_details` is not counted twice."""
    for key in FIELDS:
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    details = message.get("reasoning_details")
    if not details:
        return ""
    return "".join(str(item.get("text") or item.get("summary") or "") for item in details if isinstance(item, dict))


def thinking_disabled(payload: dict) -> bool:
    """Whether a request we built switches thinking off, in any of the shapes our catalog sends. A prefilled template then
    opens no <think> block, so the output is all answer."""
    thinking = payload.get("thinking")
    kwargs = payload.get("chat_template_kwargs")
    return (
        (isinstance(thinking, dict) and thinking.get("type") == "disabled")
        or thinking is False
        or payload.get("enable_thinking") is False
        or (isinstance(kwargs, dict) and (kwargs.get("enable_thinking") is False or kwargs.get("thinking") is False))
        or payload.get("reasoning_effort") == "none"
    )


_START, _THINK, _TEXT = 0, 1, 2


class ThinkSplitter:
    """Splits one response's text stream into (answer, thinking) as it arrives.

    - An opening tag counts only at the start of the output (after whitespace); later it is literal answer text.
    - `prefilled`: the prompt already opened the block, so the output is thinking until the closing tag. A leading opening
      tag is still accepted, for templates that let the model write it again.
    - A tag split across chunks is held back until it can be decided; nothing else is delayed.
    - Whitespace right after either tag is dropped (templates put a newline there).
    """

    __slots__ = ("_close", "_open", "_pending", "_trim", "closed", "prefilled", "state")

    def __init__(self, tag: str = "think", prefilled: bool = False) -> None:
        self._open = f"<{tag}>"
        self._close = f"</{tag}>"
        self.prefilled = prefilled
        self.state = _THINK if prefilled else _START
        self._pending = ""
        self._trim = prefilled
        self.closed = False

    def feed(self, text: str) -> tuple[str, str]:
        if self.state == _TEXT and not self._trim:
            return text, ""  # the common case once the answer has started: no work at all
        buf = self._pending + text if self._pending else text
        self._pending = ""
        answer = thinking = ""
        while buf:
            if self.state == _START:
                head = buf.lstrip()
                if not head or (len(head) < len(self._open) and self._open.startswith(head)):
                    self._pending = buf  # only whitespace, or the start of an opening tag: decide with the next chunk
                    break
                if head.startswith(self._open):
                    self.state, self._trim, buf = _THINK, True, head[len(self._open) :]
                    continue
                self.state = _TEXT
                answer += buf
                break
            if self._trim:
                buf = buf.lstrip()
                if not buf:
                    break
                if self.state == _THINK and self.prefilled and not self.closed:
                    if len(buf) < len(self._open) and self._open.startswith(buf):
                        self._pending = buf
                        break
                    if buf.startswith(self._open):
                        buf = buf[len(self._open) :]
                        continue
                self._trim = False
            if self.state == _TEXT:
                answer += buf
                break
            at = buf.find(self._close)
            if at >= 0:
                thinking += buf[:at]
                self.state, self.closed, self._trim, buf = _TEXT, True, True, buf[at + len(self._close) :]
                continue
            # Hold back only a tail that could still become the closing tag. The tag has one "<", at its start.
            lt = buf.rfind("<", max(0, len(buf) - len(self._close) + 1))
            if lt >= 0 and self._close.startswith(buf[lt:]):
                thinking += buf[:lt]
                self._pending = buf[lt:]
            else:
                thinking += buf
            break
        return answer, thinking

    def finish(self) -> tuple[str, str]:
        """Whatever was held back when the stream ends."""
        pending, self._pending = self._pending, ""
        if self.state == _THINK:
            return "", pending
        return pending, ""

    def bypass(self) -> str:
        """The upstream separates thinking itself (a reasoning field arrived): stop splitting; returns held-back text."""
        pending, self._pending = self._pending, ""
        self.state, self._trim, self.prefilled = _TEXT, False, False
        return pending

    @property
    def never_closed_prefill(self) -> bool:
        """A prefilled block that never closed: the model answered without thinking (e.g. thinking switched off upstream),
        so everything taken as thinking was the answer."""
        return self.prefilled and not self.closed


def split_think(text: str, tag: str, prefilled: bool) -> tuple[str, str]:
    """(answer, thinking) of a complete, non-streamed text."""
    splitter = ThinkSplitter(tag, prefilled)
    answer, thinking = splitter.feed(text)
    tail_answer, tail_thinking = splitter.finish()
    answer, thinking = answer + tail_answer, thinking + tail_thinking
    if splitter.never_closed_prefill:
        return thinking + answer, ""
    return answer, thinking
