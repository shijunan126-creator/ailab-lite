"""Bounded text windows, ported from Pi's ``truncate.ts``.

Two independent limits, whichever is hit first: 2000 lines or 50KB. Output never
ends with half a line, except the tail case where one line alone exceeds the byte
cap (bash output: the end of the line is the useful part).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_LENGTH = 500  # a single match line is cut here so grep output stays compact


def format_size(n: int) -> str:
    """Sizes as the notices spell them: ``512B``, ``1.5KB``, ``2.0MB``."""
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{_to_fixed_1(n / 1024)}KB"
    return f"{_to_fixed_1(n / (1024 * 1024))}MB"


def _to_fixed_1(value: float) -> str:
    # toFixed(1) rounds halves up; Python's format() rounds them to even.
    return str(math.floor(value * 10 + 0.5) / 10)


@dataclass(frozen=True, slots=True)
class Truncation:
    content: str
    truncated: bool
    truncated_by: str | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    last_line_partial: bool
    first_line_exceeds_limit: bool
    max_lines: int
    max_bytes: int


def byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def split_lines_for_counting(content: str) -> list[str]:
    """Lines as the limits count them: a trailing newline does not start an empty line."""
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def truncate_head(content: str, max_lines: int | None = None, max_bytes: int | None = None) -> Truncation:
    """Keep the beginning: for file reads, where the start is what the reader came for."""
    max_lines = DEFAULT_MAX_LINES if max_lines is None else max_lines
    max_bytes = DEFAULT_MAX_BYTES if max_bytes is None else max_bytes
    total_bytes = byte_len(content)
    lines = split_lines_for_counting(content)
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return Truncation(content, False, None, total_lines, total_bytes, total_lines, total_bytes, False, False, max_lines, max_bytes)

    if lines and byte_len(lines[0]) > max_bytes:
        # A single line over the cap is never worth sending: point at a byte window instead.
        return Truncation("", True, "bytes", total_lines, total_bytes, 0, 0, False, True, max_lines, max_bytes)

    kept: list[str] = []
    used = 0
    truncated_by = "lines"
    for index, line in enumerate(lines[:max_lines]):
        line_bytes = byte_len(line) + (1 if index > 0 else 0)  # the newline that joins it to the line before
        if used + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        kept.append(line)
        used += line_bytes
    if len(kept) >= max_lines and used <= max_bytes:
        truncated_by = "lines"

    output = "\n".join(kept)
    return Truncation(output, True, truncated_by, total_lines, total_bytes, len(kept), byte_len(output), False, False, max_lines, max_bytes)


def truncate_tail(content: str, max_lines: int | None = None, max_bytes: int | None = None) -> Truncation:
    """Keep the end: for command output, where errors and results are."""
    max_lines = DEFAULT_MAX_LINES if max_lines is None else max_lines
    max_bytes = DEFAULT_MAX_BYTES if max_bytes is None else max_bytes
    total_bytes = byte_len(content)
    lines = split_lines_for_counting(content)
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return Truncation(content, False, None, total_lines, total_bytes, total_lines, total_bytes, False, False, max_lines, max_bytes)

    kept: list[str] = []
    used = 0
    truncated_by = "lines"
    last_line_partial = False
    for line in reversed(lines):
        if len(kept) >= max_lines:
            break
        line_bytes = byte_len(line) + (1 if kept else 0)
        if used + line_bytes > max_bytes:
            truncated_by = "bytes"
            if not kept:
                # Nothing fits whole: the end of this line is the part worth keeping.
                partial = truncate_string_to_bytes_from_end(line, max_bytes)
                kept.insert(0, partial)
                used = byte_len(partial)
                last_line_partial = True
            break
        kept.insert(0, line)
        used += line_bytes
    if len(kept) >= max_lines and used <= max_bytes:
        truncated_by = "lines"

    output = "\n".join(kept)
    return Truncation(
        output, True, truncated_by, total_lines, total_bytes, len(kept), byte_len(output), last_line_partial, False, max_lines, max_bytes
    )


def truncate_string_to_bytes_from_end(text: str, max_bytes: int) -> str:
    """The last ``max_bytes`` of ``text``, cut on a UTF-8 character boundary."""
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    start = len(data) - max_bytes
    while start < len(data) and (data[start] & 0xC0) == 0x80:
        start += 1
    return data[start:].decode("utf-8")


def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> tuple[str, bool]:
    """Cut one match line to ``max_chars`` characters, marking the cut."""
    if len(line) <= max_chars:
        return line, False
    return f"{slice_head(line, max_chars)}... [truncated]", True


def slice_head(text: str, max_units: int) -> str:
    """At most the first ``max_units`` characters, never splitting a surrogate pair."""
    end = max(0, max_units)
    if end and is_high_surrogate(ord(text[end - 1])) and end < len(text) and is_low_surrogate(ord(text[end])):
        return text[: end - 1]
    return text[:end]


def slice_tail(text: str, max_units: int) -> str:
    if max_units <= 0:
        return ""
    start = max(0, len(text) - max_units)
    if start and is_low_surrogate(ord(text[start])) and is_high_surrogate(ord(text[start - 1])):
        return text[start + 1 :]
    return text[start:]


def is_high_surrogate(code: int) -> bool:
    return 0xD800 <= code <= 0xDBFF


def is_low_surrogate(code: int) -> bool:
    return 0xDC00 <= code <= 0xDFFF


@dataclass(frozen=True, slots=True)
class LineWindow:
    text: str
    total_lines: int
    truncation: Truncation
    next_offset: int | None


def read_lines(text: str, path: str, offset: int | None = None, limit: int | None = None) -> LineWindow:
    """A 1-indexed line window of ``text`` with the continuation notice Pi writes.

    ``offset``/``limit`` count lines, not characters. Raises ``ValueError`` with Pi's
    wording when the offset is past the end.
    """
    lines = text.split("\n")  # a trailing newline leaves one empty last line, as Pi counts it
    total_lines = len(lines)
    start = 0 if offset is None else max(0, offset - 1)
    if start >= total_lines:
        raise ValueError(f"Offset {offset} is beyond end of file ({total_lines} lines total)")
    end = total_lines if limit is None else min(start + limit, total_lines)
    truncation = truncate_head("\n".join(lines[start:end]))
    first_line_bytes = byte_len(lines[start]) if truncation.first_line_exceeds_limit else 0
    shown_start = start + 1

    if truncation.first_line_exceeds_limit:
        size = format_size(first_line_bytes)
        body = (
            f"[Line {shown_start} is {size}, exceeds {format_size(DEFAULT_MAX_BYTES)} limit. "
            f"Use bash: sed -n '{shown_start}p' {path} | head -c {DEFAULT_MAX_BYTES}]"
        )
        return LineWindow(body, total_lines, truncation, None)

    if truncation.truncated:
        shown_end = shown_start + truncation.output_lines - 1
        next_offset = shown_end + 1
        if truncation.truncated_by == "lines":
            notice = f"[Showing lines {shown_start}-{shown_end} of {total_lines}. Use offset={next_offset} to continue.]"
        else:
            notice = (
                f"[Showing lines {shown_start}-{shown_end} of {total_lines} "
                f"({format_size(DEFAULT_MAX_BYTES)} limit). Use offset={next_offset} to continue.]"
            )
        return LineWindow(f"{truncation.content}\n\n{notice}", total_lines, truncation, next_offset)

    if limit is not None:
        user_limited = min(start + limit, total_lines) - start
        if start + user_limited < total_lines:
            remaining = total_lines - (start + user_limited)
            next_offset = start + user_limited + 1
            notice = f"[{remaining} more lines in file. Use offset={next_offset} to continue.]"
            return LineWindow(f"{truncation.content}\n\n{notice}", total_lines, truncation, next_offset)

    return LineWindow(truncation.content, total_lines, truncation, None)
