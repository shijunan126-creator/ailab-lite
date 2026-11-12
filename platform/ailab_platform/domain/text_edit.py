"""Exact-text file editing, ported from Pi's ``edit-diff.ts``.

Every ``oldText`` is matched against the original content, must be unique there,
and must not overlap another edit in the same call. Matching is exact first and
then tolerant to the differences a model reliably introduces when it quotes a
file back: trailing whitespace, curly quotes, Unicode dashes, special spaces and
width/compatibility forms.

Tolerance never rewrites the file. Every fold maps one character to one character,
so ``str.translate`` preserves length and index: the match runs on translated text
and its offsets are already file offsets, and a full-width comma next to the
matched span keeps its own bytes. Trailing whitespace is handled by stripping line
ends once and remembering where, not by a per-character map.

Matching is linearithmic, not quadratic: ``str.find`` is CPython's two-way search,
while a regex made of character classes is backtracking over "file length times
oldText length" (14 s for 2 MB of repeated text). It runs in a worker thread, but
that does not make it non-blocking: CPython's ``re`` holds the GIL for the whole
search. Nothing here hides the GIL either, so the cost has to be small.

Deliberate differences from Pi:

* Pi normalizes the whole file and writes the normalized copy back, which turns
  every full-width mark on the touched lines into half width.
* Combining sequences (``e`` + U+0301) are not folded: models quote composed
  text, and supporting it would put the per-character cost back.
"""

from __future__ import annotations

import itertools
import unicodedata
from array import array
from bisect import bisect_left
from dataclasses import dataclass

BOM = "\ufeff"
OCCURRENCE_REPORT_CAP = 100  # a repeated line can occur thousands of times; the message stays readable


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

_folds: dict[str, str] | None = None
_variants: dict[str, list[str]] | None = None
_table: dict[int, str] | None = None


def _fold_tables() -> tuple[dict[str, str], dict[str, list[str]]]:
    """Every one-to-one fold and its reverse map, built on first fuzzy match.

    Scanning 1.1M code points costs ~150 ms, which is too much for module import on
    every service start; an exact match never needs the tables at all. A fold is kept
    only when it maps one character to one character: a full-width digit folds, the
    ``fi`` ligature would become two characters and is left alone.
    """
    global _folds, _variants
    if _folds is None:
        folds: dict[str, str] = {}
        for code in range(0x110000):
            if 0xD800 <= code <= 0xDFFF:  # lone surrogates cannot appear in decoded text
                continue
            char = chr(code)
            mapped = _FUZZY_TABLE.get(code)
            if mapped is None:
                folded = unicodedata.normalize("NFKC", char)
                if len(folded) != 1 or folded == char:
                    continue
                mapped = folded
            folds[char] = mapped
        variants: dict[str, list[str]] = {}
        for source, folded in folds.items():
            variants.setdefault(folded, [folded]).append(source)
        _folds, _variants = folds, variants
    return _folds, _variants


def folded_text(text: str) -> str:
    """The text with every one-to-one fold applied; used to compare short strings."""
    folds, _ = _fold_tables()
    return "".join(folds.get(char, char) for char in text)


def find_all(haystack: str, needle: str, limit: int | None = None) -> list[int]:
    """Every occurrence, including overlapping ones: "a\na\n" occurs twice in "a\na\na\n".

    ``limit`` stops the scan once enough occurrences are known: a repeated line can occur
    millions of times and the message only needs to know the text is not unique.
    """
    if not needle:
        return []
    found: list[int] = []
    index = haystack.find(needle)
    while index != -1:
        found.append(index)
        if limit is not None and len(found) >= limit:
            break
        index = haystack.find(needle, index + 1)
    return found


def _translate_table() -> dict[int, str]:
    """The fold table in ``str.translate`` form: one character in, one character out."""
    global _table
    if _table is None:
        folds, _ = _fold_tables()
        _table = {ord(source): folded for source, folded in folds.items()}
    return _table


def has_line_end_whitespace(text: str) -> bool:
    """Whether some line ends with a space or a tab (the last line counts, unterminated)."""
    if text.endswith((" ", "\t")):
        return True
    return " \n" in text or "\t\n" in text


def strip_line_ends(text: str) -> tuple[str, array, array]:
    """``text`` without the spaces/tabs before each line break, plus where they were removed.

    ``points[i]`` is the offset in the stripped text of the i-th removal and ``amounts[i]``
    the total removed up to and including it, so ``offset + removed_before(offset)`` maps
    a stripped offset back to the original one.
    """
    removed_ranges: list[tuple[int, int]] = []
    position = 0
    length = len(text)
    while position < length:
        newline = text.find("\n", position)
        end = length if newline == -1 else newline
        keep = end
        while keep > position and text[keep - 1] in " \t":
            keep -= 1
        if keep != end:
            removed_ranges.append((keep, end))
        if newline == -1:
            break
        position = newline + 1
    if not removed_ranges:
        return text, array("l"), array("l")
    parts: list[str] = []
    points, amounts = array("l"), array("l")
    previous, removed = 0, 0
    for keep, end in removed_ranges:
        parts.append(text[previous:keep])
        removed += end - keep
        points.append(keep - (removed - (end - keep)))
        amounts.append(removed)
        previous = end
    parts.append(text[previous:])
    return "".join(parts), points, amounts


def _removed_before(offset: int, points: array, amounts: array) -> int:
    index = bisect_left(points, offset)
    return amounts[index - 1] if index else 0


def _fuzzy_spans(content: str, old_text: str) -> list[tuple[int, int]]:
    """Every tolerant occurrence of ``old_text`` in ``content`` as ``(start, end)`` file offsets."""
    table = _translate_table()
    # Every fold source is a non-ASCII character and NFKC leaves ASCII alone, so an ASCII
    # document folds to itself: skip the translation, which costs 130 ms for 2 MB.
    folded = content if content.isascii() else content.translate(table)
    needle = old_text if old_text.isascii() else old_text.translate(table)
    lines = needle.split("\n")
    stripped = "\n".join(line.rstrip(" \t") for line in lines)
    if not stripped:
        return []  # only whitespace: its pattern would match everywhere, which is never what was meant
    trailing = lines[-1] != lines[-1].rstrip(" \t")
    spans: list[tuple[int, int]] = []
    if not has_line_end_whitespace(folded):
        stripped_file, points, amounts = folded, array("l"), array("l")
    else:
        stripped_file, points, amounts = strip_line_ends(folded)
    for hit in find_all(stripped_file, stripped, limit=OCCURRENCE_REPORT_CAP):
        start = hit + _removed_before(hit, points, amounts)
        end = hit + len(stripped) + _removed_before(hit + len(stripped), points, amounts)
        if trailing:  # quoted trailing spaces must be consumed, not left behind
            while end < len(folded) and folded[end] in " \t":
                end += 1
        spans.append((start, end))
    return spans


def _missing_message(path: str, index: int, total: int) -> str:
    if total == 1:
        return f"Could not find the exact text in {path}. The old text must match exactly including all whitespace and newlines."
    return f"Could not find edits[{index}] in {path}. The oldText must match exactly including all whitespace and newlines."


def _occurrences_message(path: str, index: int, total: int, occurrences: int) -> str:
    count = f"{occurrences}" if occurrences < OCCURRENCE_REPORT_CAP else f"at least {OCCURRENCE_REPORT_CAP}"
    if total == 1:
        return f"Found {count} occurrences of the text in {path}. The text must be unique. Please provide more context to make it unique."
    return (
        f"Found {count} occurrences of edits[{index}] in {path}. "
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

    matched: list[TextReplacement] = []
    for index, (old_text, new_text) in enumerate(normalized):
        exact = find_all(base_content, old_text, limit=OCCURRENCE_REPORT_CAP)
        if len(exact) == 1:
            matched.append(TextReplacement(exact[0], len(old_text), new_text, index))
            continue
        if len(exact) > 1:
            raise EditError(_occurrences_message(path, index, total, min(len(exact), OCCURRENCE_REPORT_CAP)))
        spans = _fuzzy_spans(base_content, old_text)
        if not spans:
            raise EditError(_missing_message(path, index, total))
        if len(spans) > 1:
            raise EditError(_occurrences_message(path, index, total, len(spans)))
        start, end = spans[0]
        # The span covers trailing whitespace and width variants too, so it is the replacement span.
        matched.append(TextReplacement(start, end - start, new_text, index))

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
