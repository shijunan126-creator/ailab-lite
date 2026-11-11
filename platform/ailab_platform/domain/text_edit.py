"""Exact-text file editing, ported from Pi's ``edit-diff.ts``.

Every ``oldText`` is matched against the original content, must be unique there,
and must not overlap another edit in the same call. Matching is exact first and
then tolerant to the differences a model reliably introduces when it quotes a
file back: trailing whitespace, curly quotes, Unicode dashes, special spaces and
width/compatibility forms. Tolerance never widens the *set* of matches, so a
text that occurs twice is still refused.

Tolerance is built character by character with a source span for every folded
character, so a fuzzy match is applied to the bytes it actually matched. Pi
rewrites the whole file from its normalized copy, which silently converts every
full-width comma, digit and punctuation mark on the touched lines to half width;
keeping the spans means an edit of one quote never rewrites its neighbours.
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

_TRIM = frozenset(" \t")


@dataclass(frozen=True, slots=True)
class _Unit:
    """One character of the folded text and the source span it came from."""

    char: str
    start: int
    end: int


def _fold(char: str) -> str:
    mapped = _FUZZY_TABLE.get(ord(char))
    if mapped is not None:
        return mapped
    composed = unicodedata.normalize("NFKC", char)
    # Only 1:1 folds keep one source character per folded character: a full-width digit folds,
    # a compatibility ligature ("fi" written as one glyph) does not.
    return composed if len(composed) == 1 else char


def _trim_line_ends(units: list[_Unit]) -> list[_Unit]:
    """Drop whitespace a model leaves at the end of a line; kept characters keep their own offsets."""
    kept: list[_Unit] = []
    for unit in units:
        if unit.char in _TRIM:
            kept.append(unit)
            continue
        if unit.char == "\n":
            while kept and kept[-1].char in _TRIM:
                kept.pop()
        kept.append(unit)
    while kept and kept[-1].char in _TRIM:
        kept.pop()
    return kept


def folded_units(text: str) -> list[_Unit]:
    units: list[_Unit] = []
    for index, char in enumerate(text):
        # A combining mark folds into the character before it ("e" + U+0301 -> "é").
        if units and unicodedata.combining(char):
            composed = unicodedata.normalize("NFKC", units[-1].char + char)
            if len(composed) == 1:
                units[-1] = _Unit(composed, units[-1].start, index + 1)
                continue
        units.append(_Unit(_fold(char), index, index + 1))
    return _trim_line_ends(units)


def folded_text(text: str) -> str:
    return "".join(unit.char for unit in folded_units(text))


def find_all(haystack: str, needle: str) -> list[int]:
    """Every occurrence, including overlapping ones: "}\\n}\\n" occurs twice in "}\\n}\\n}\\n"."""
    if not needle:
        return []
    found: list[int] = []
    index = haystack.find(needle)
    while index != -1:
        found.append(index)
        index = haystack.find(needle, index + 1)
    return found


def _missing_message(path: str, index: int, total: int) -> str:
    if total == 1:
        return f"Could not find the exact text in {path}. The old text must match exactly including all whitespace and newlines."
    return f"Could not find edits[{index}] in {path}. The oldText must match exactly including all whitespace and newlines."


def _occurrences_message(path: str, index: int, total: int, occurrences: int) -> str:
    if total == 1:
        return (
            f"Found {occurrences} occurrences of the text in {path}. "
            "The text must be unique. Please provide more context to make it unique."
        )
    return (
        f"Found {occurrences} occurrences of edits[{index}] in {path}. "
        "Each oldText must be unique. Please provide more context to make it unique."
    )


def _apply_replacements(content: str, replacements: list[TextReplacement]) -> str:
    result = content
    for replacement in reversed(replacements):
        index = replacement.match_index
        result = result[:index] + replacement.new_text + result[index + replacement.match_length :]
    return result


def apply_edits(base_content: str, edits: list[tuple[str, str]], path: str) -> EditResult:
    """Match and apply every edit against the same original content."""
    normalized = [(normalize_to_lf(old), normalize_to_lf(new)) for old, new in edits]
    total = len(normalized)
    for index, (old_text, _) in enumerate(normalized):
        if not old_text:
            raise EditError(
                f"oldText must not be empty in {path}." if total == 1 else f"edits[{index}].oldText must not be empty in {path}."
            )

    units: list[_Unit] | None = None
    folded: str | None = None
    matched: list[TextReplacement] = []
    for index, (old_text, new_text) in enumerate(normalized):
        exact = find_all(base_content, old_text)
        if len(exact) == 1:
            matched.append(TextReplacement(exact[0], len(old_text), new_text, index))
            continue
        if len(exact) > 1:
            raise EditError(_occurrences_message(path, index, total, len(exact)))
        if units is None:
            units = folded_units(base_content)
            folded = "".join(unit.char for unit in units)
        assert folded is not None
        needle = folded_text(old_text)
        found = find_all(folded, needle)
        if not found:
            raise EditError(_missing_message(path, index, total))
        if len(found) > 1:
            raise EditError(_occurrences_message(path, index, total, len(found)))
        start = units[found[0]].start
        stop = units[found[0] + len(needle) - 1].end
        matched.append(TextReplacement(start, stop - start, new_text, index))

    matched.sort(key=lambda item: item.match_index)
    for previous, current in itertools.pairwise(matched):
        if previous.match_index + previous.match_length > current.match_index:
            raise EditError(
                f"edits[{previous.edit_index}] and edits[{current.edit_index}] overlap in {path}. "
                "Merge them into one edit or target disjoint regions."
            )

    new_content = _apply_replacements(base_content, matched)
    if new_content == base_content:
        if total == 1:
            raise EditError(
                f"No changes made to {path}. The replacement produced identical content. "
                "This might indicate an issue with special characters or the text not existing as expected."
            )
        raise EditError(f"No changes made to {path}. The replacements produced identical content.")
    return EditResult(base_content, new_content, tuple(matched))


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
