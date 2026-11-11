"""Exact-text file editing, ported from Pi's ``edit-diff.ts``.

Every ``oldText`` is matched against the original content, must be unique there,
and must not overlap another edit in the same call. Matching is exact first and
then tolerant to the differences a model reliably introduces when it quotes a
file back: trailing whitespace, curly quotes, Unicode dashes, special spaces and
width/compatibility forms.

Tolerance never rewrites the file: ``oldText`` is compiled into one regex whose
character classes accept every character that folds to the requested one, and it
is searched directly in the original bytes. Matched offsets are therefore file
offsets, a fuzzy edit costs nothing beyond the pattern (a 5 MB file matches in
tens of milliseconds, no per-character table and no second copy of the file), and
a full-width comma next to the matched span keeps its own bytes.

Deliberate differences from Pi:

* Pi normalizes the whole file and writes the normalized copy back, which turns
  every full-width mark on the touched lines into half width.
* Combining sequences (``e`` + U+0301) are not folded: models quote composed
  text, and supporting it would put the per-character cost back.
"""

from __future__ import annotations

import itertools
import re
import unicodedata
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


def _atom(char: str) -> str:
    """One character of ``oldText``: every file character that folds to the same one."""
    folds, variants = _fold_tables()
    group = variants.get(folds.get(char, char))
    if group is None:
        return re.escape(char)
    return "[" + "".join(re.escape(item) for item in group) + "]"


def _space_class() -> str:
    _, variants = _fold_tables()
    return "[" + "".join(re.escape(item) for item in variants.get(" ", [" "])) + r"\t]*"


def fuzzy_pattern(old_text: str) -> re.Pattern[str] | None:
    """``oldText`` as a regex over the original file: trailing whitespace optional, widths folded.

    Returns ``None`` for a text that is only whitespace (its pattern would match everywhere).
    """
    space = _space_class()
    lines = old_text.split("\n")
    body = (space + "\n").join("".join(_atom(char) for char in line.rstrip(" \t")) for line in lines)
    if not body:
        return None
    if lines[-1] != lines[-1].rstrip(" \t"):
        body += space  # the quoted trailing spaces must be consumed, not left behind
    return re.compile(body)


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


def _fuzzy_starts(pattern: re.Pattern[str], content: str) -> list[int]:
    """Overlapping start offsets, stopped once the text is known not to be unique."""
    starts: list[int] = []
    position = 0
    while len(starts) < OCCURRENCE_REPORT_CAP:
        found = pattern.search(content, position)
        if found is None:
            break
        starts.append(found.start())
        position = found.start() + 1
    return starts


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
        exact = find_all(base_content, old_text)
        if len(exact) == 1:
            matched.append(TextReplacement(exact[0], len(old_text), new_text, index))
            continue
        if len(exact) > 1:
            raise EditError(_occurrences_message(path, index, total, min(len(exact), OCCURRENCE_REPORT_CAP)))
        pattern = fuzzy_pattern(old_text)
        starts = _fuzzy_starts(pattern, base_content) if pattern is not None else []
        if not starts:
            raise EditError(_missing_message(path, index, total))
        if len(starts) > 1:
            raise EditError(_occurrences_message(path, index, total, len(starts)))
        # The regex matched trailing whitespace and width variants too, so its span is the replacement span.
        found = pattern.search(base_content, starts[0])
        assert found is not None and found.start() == starts[0]
        matched.append(TextReplacement(found.start(), found.end() - found.start(), new_text, index))

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
