"""Exact-text file editing, ported from Pi's ``edit-diff.ts``.

Every ``oldText`` is matched against the original content, must be unique there,
and must not overlap another edit in the same call. Matching is exact first and
then tolerant to the differences a model reliably introduces when it quotes a
file back: trailing whitespace, curly quotes, Unicode dashes, special spaces and
compatibility forms (NFKC). Tolerance never widens the *set* of matches, so a
text that occurs twice is still refused.

The port keeps Pi's byte-preservation rule: matched offsets live in the LF
normalized text, but the write is spliced into the original, so untouched lines
keep their own line endings and a BOM survives.
"""

from __future__ import annotations

import itertools
import unicodedata
from dataclasses import dataclass

BOM = "\ufeff"


class EditError(ValueError):
    """A refused edit; the message is what the model reads."""


@dataclass(frozen=True, slots=True)
class TextReplacement:
    match_index: int
    match_length: int
    new_text: str
    edit_index: int = 0


@dataclass(frozen=True, slots=True)
class FuzzyMatch:
    found: bool
    index: int
    match_length: int
    used_fuzzy: bool
    content_for_replacement: str


@dataclass(frozen=True, slots=True)
class EditResult:
    base_content: str
    new_content: str
    replacements: tuple[TextReplacement, ...]


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_bom(text: str) -> tuple[str, str]:
    return (BOM, text[1:]) if text.startswith(BOM) else ("", text)


_SINGLE_QUOTES = "\u2018\u2019\u201a\u201b"
_DOUBLE_QUOTES = "\u201c\u201d\u201e\u201f"
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_SPACES = "\u00a0" + "".join(chr(code) for code in range(0x2002, 0x200B)) + "\u202f\u205f\u3000"

_FUZZY_TABLE = {
    **{ord(char): "'" for char in _SINGLE_QUOTES},
    **{ord(char): '"' for char in _DOUBLE_QUOTES},
    **{ord(char): "-" for char in _DASHES},
    **{ord(char): " " for char in _SPACES},
}


def normalize_for_fuzzy_match(text: str) -> str:
    """The one normalization both the file and ``oldText`` go through before a retry."""
    return "\n".join(line.rstrip() for line in unicodedata.normalize("NFKC", text).split("\n")).translate(_FUZZY_TABLE)


def fuzzy_find_text(content: str, old_text: str) -> FuzzyMatch:
    exact = content.find(old_text)
    if exact != -1:
        return FuzzyMatch(True, exact, len(old_text), False, content)
    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old_text = normalize_for_fuzzy_match(old_text)
    fuzzy_index = fuzzy_content.find(fuzzy_old_text)
    if fuzzy_index == -1:
        return FuzzyMatch(False, -1, 0, False, content)
    return FuzzyMatch(True, fuzzy_index, len(fuzzy_old_text), True, fuzzy_content)


def count_occurrences(content: str, old_text: str) -> int:
    return normalize_for_fuzzy_match(content).count(normalize_for_fuzzy_match(old_text))


def _split_lines_with_endings(content: str) -> list[str]:
    if not content:
        return []
    parts: list[str] = []
    start = 0
    while start < len(content):
        line_feed = content.find("\n", start)
        if line_feed == -1:
            parts.append(content[start:])
            break
        parts.append(content[start : line_feed + 1])
        start = line_feed + 1
    return parts


def _line_spans(content: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in _split_lines_with_endings(content):
        spans.append((offset, offset + len(line)))
        offset += len(line)
    return spans


def _replacement_line_range(spans: list[tuple[int, int]], replacement: TextReplacement) -> tuple[int, int]:
    start = replacement.match_index
    end = start + replacement.match_length
    start_line = next((index for index, (left, right) in enumerate(spans) if left <= start < right), -1)
    if start_line == -1:
        raise EditError("Replacement range is outside the base content.")
    end_line = start_line
    while end_line < len(spans) and spans[end_line][1] < end:
        end_line += 1
    if end_line >= len(spans):
        raise EditError("Replacement range is outside the base content.")
    return start_line, end_line + 1


def _apply_replacements(content: str, replacements: list[TextReplacement], offset: int = 0) -> str:
    result = content
    for replacement in reversed(replacements):
        index = replacement.match_index - offset
        result = result[:index] + replacement.new_text + result[index + replacement.match_length :]
    return result


def _widen_replacements_to_lines(original_content: str, base_content: str, replacements: list[TextReplacement]) -> list[TextReplacement]:
    """Rewrite only the lines a replacement touches, and take the rest from the original.

    A fuzzy match leaves trailing whitespace and compatibility forms normalized;
    copying the surviving lines from the original keeps those bytes.
    """
    original_spans = _line_spans(original_content)
    base_spans = _line_spans(base_content)
    if len(original_spans) != len(base_spans):
        raise EditError("Cannot preserve unchanged lines because the base content has a different line count.")

    groups: list[dict] = []
    for replacement in sorted(replacements, key=lambda item: item.match_index):
        start_line, end_line = _replacement_line_range(base_spans, replacement)
        current = groups[-1] if groups else None
        if current is not None and start_line < current["end_line"]:
            current["end_line"] = max(current["end_line"], end_line)
            current["replacements"].append(replacement)
            continue
        groups.append({"start_line": start_line, "end_line": end_line, "replacements": [replacement]})

    widened: list[TextReplacement] = []
    for group in groups:
        first, last = group["start_line"], group["end_line"] - 1
        group_start = base_spans[first][0]
        group_end = base_spans[last][1]
        original_start = original_spans[first][0]
        widened.append(
            TextReplacement(
                match_index=original_start,
                match_length=original_spans[last][1] - original_start,
                new_text=_apply_replacements(base_content[group_start:group_end], group["replacements"], group_start),
            )
        )
    return widened


def apply_edits(normalized_content: str, edits: list[tuple[str, str]], path: str) -> EditResult:
    """Match and apply every edit against the same original content."""
    normalized = [(normalize_to_lf(old), normalize_to_lf(new)) for old, new in edits]
    total = len(normalized)
    for index, (old_text, _) in enumerate(normalized):
        if not old_text:
            raise EditError(
                f"oldText must not be empty in {path}." if total == 1 else f"edits[{index}].oldText must not be empty in {path}."
            )

    used_fuzzy = any(fuzzy_find_text(normalized_content, old_text).used_fuzzy for old_text, _ in normalized)
    replacement_base = normalize_for_fuzzy_match(normalized_content) if used_fuzzy else normalized_content

    matched: list[TextReplacement] = []
    for index, (old_text, new_text) in enumerate(normalized):
        found = fuzzy_find_text(replacement_base, old_text)
        if not found.found:
            if total == 1:
                raise EditError(
                    f"Could not find the exact text in {path}. The old text must match exactly including all whitespace and newlines."
                )
            raise EditError(
                f"Could not find edits[{index}] in {path}. The oldText must match exactly including all whitespace and newlines."
            )
        occurrences = count_occurrences(replacement_base, old_text)
        if occurrences > 1:
            if total == 1:
                raise EditError(
                    f"Found {occurrences} occurrences of the text in {path}. "
                    "The text must be unique. Please provide more context to make it unique."
                )
            raise EditError(
                f"Found {occurrences} occurrences of edits[{index}] in {path}. "
                "Each oldText must be unique. Please provide more context to make it unique."
            )
        matched.append(TextReplacement(found.index, found.match_length, new_text, index))

    matched.sort(key=lambda item: item.match_index)
    for previous, current in itertools.pairwise(matched):
        if previous.match_index + previous.match_length > current.match_index:
            raise EditError(
                f"edits[{previous.edit_index}] and edits[{current.edit_index}] overlap in {path}. "
                "Merge them into one edit or target disjoint regions."
            )

    replacements = _widen_replacements_to_lines(normalized_content, replacement_base, matched) if used_fuzzy else matched
    new_content = _apply_replacements(normalized_content, replacements)
    if new_content == normalized_content:
        if total == 1:
            raise EditError(
                f"No changes made to {path}. The replacement produced identical content. "
                "This might indicate an issue with special characters or the text not existing as expected."
            )
        raise EditError(f"No changes made to {path}. The replacements produced identical content.")
    return EditResult(normalized_content, new_content, tuple(replacements))


def _line_ending_at(content: str, offset: int) -> str:
    """The line ending of the line containing ``offset``.

    An unterminated last line borrows the previous line's ending. A lone CR only
    counts as a line ending in a file with no LF at all; elsewhere it is content.
    """
    line_feed = content.find("\n", offset)
    if line_feed == -1:
        line_feed = content.rfind("\n", 0, offset + 1)
    if line_feed == -1:
        return "\r" if "\r" in content else "\n"
    return "\r\n" if line_feed > 0 and content[line_feed - 1] == "\r" else "\n"


def apply_replacements_to_original(original_content: str, replacements: tuple[TextReplacement, ...] | list[TextReplacement]) -> str:
    """Splice replacements matched in LF-normalized text back into the original bytes.

    A CRLF pair is two characters where the normalized text has one, so an offset
    in the normalized text is shifted by the number of CRLFs before it. Inserted
    line breaks take the ending of the line they replace, and a kept lone CR
    followed by an inserted LF is separated so the two cannot merge into a CRLF.
    """
    crlf_before = 0
    next_crlf = original_content.find("\r\n")

    def to_original(offset: int) -> int:
        nonlocal crlf_before, next_crlf
        while next_crlf != -1 and next_crlf - crlf_before < offset:
            crlf_before += 1
            next_crlf = original_content.find("\r\n", next_crlf + 2)
        return offset + crlf_before

    parts: list[str] = []
    ends_with_cr = False

    def push(part: str) -> None:
        nonlocal ends_with_cr
        if not part:
            return
        if ends_with_cr and part[0] == "\n":
            parts.append("\n")
        parts.append(part)
        ends_with_cr = part[-1] == "\r"

    position = 0
    for replacement in replacements:
        start = to_original(replacement.match_index)
        push(original_content[position:start])
        ending = _line_ending_at(original_content, start)
        push(replacement.new_text if ending == "\n" else replacement.new_text.replace("\n", ending))
        position = to_original(replacement.match_index + replacement.match_length)
    push(original_content[position:])
    return "".join(parts)


def edit_text(raw: str, edits: list[tuple[str, str]], path: str) -> str:
    """The whole edit for one file: strip BOM, match in LF space, write back in the file's own bytes."""
    bom, content = split_bom(raw)
    normalized = normalize_to_lf(content)
    result = apply_edits(normalized, edits, path)
    final = result.new_content if content == normalized else apply_replacements_to_original(content, result.replacements)
    return bom + final
