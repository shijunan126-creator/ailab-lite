"""Exact-text editing, matching Pi's ``edit`` behaviour case for case."""

from __future__ import annotations

import itertools
import re
import threading
import time
import tracemalloc

import pytest

from ailab_platform.domain.text_edit import (
    EditError,
    TextReplacement,
    apply_edits,
    apply_replacements_to_original,
    edit_text,
    normalize_to_lf,
)


def edit(content: str, edits, path: str = "/workspace/work/a.txt") -> str:
    return edit_text(content, edits, path)


def test_replaces_a_unique_region():
    assert edit("const x = 'exact';\nconst y = 'other';\n", [("const x = 'exact';", "const x = 'changed';")]) == (
        "const x = 'changed';\nconst y = 'other';\n"
    )


def test_rejects_text_that_occurs_twice():
    with pytest.raises(EditError, match=re.escape("Found 2 occurrences of the text in /workspace/work/a.txt")):
        edit("same\nsame\n", [("same", "other")])


def test_names_the_edit_when_several_are_sent():
    with pytest.raises(EditError, match=r"Found 2 occurrences of edits\[1\]"):
        edit("a1\na2\nb\nb\n", [("a1", "A1"), ("b", "B")])


def test_rejects_text_that_is_absent():
    with pytest.raises(EditError, match="Could not find the exact text"):
        edit("completely different content\n", [("this does not exist", "replacement")])


def test_rejects_an_empty_old_text():
    with pytest.raises(EditError, match="oldText must not be empty"):
        edit("content\n", [("", "x")])


def test_rejects_overlapping_edits():
    with pytest.raises(EditError, match=r"edits\[0\] and edits\[1\] overlap"):
        edit("one\ntwo\nthree\n", [("one\ntwo\n", "ONE\nTWO\n"), ("two\nthree\n", "TWO\nTHREE\n")])


def test_every_edit_matches_the_original_not_the_running_result():
    # "bar" also appears in the result of the first edit, so an incremental implementation would misplace it.
    assert edit("foo\nbar\nbaz\n", [("foo\n", "foo bar\n"), ("bar\n", "BAR\n")]) == "foo bar\nBAR\nbaz\n"


def test_no_partial_write_when_one_edit_fails():
    with pytest.raises(EditError):
        edit("keep me\n", [("keep", "KEEP"), ("missing", "x")])


def test_identical_replacement_is_refused():
    with pytest.raises(EditError, match="No changes made to"):
        edit("content\n", [("content", "content")])


def test_multiple_disjoint_edits_in_one_call():
    assert edit("a\nb\nc\nd\n", [("a", "A"), ("c", "C")]) == "A\nb\nC\nd\n"


class TestFuzzyMatching:
    def test_trailing_whitespace(self):
        assert edit("line one   \nline two  \nline three\n", [("line one\nline two\n", "replaced\n")]) == ("replaced\nline three\n")

    def test_fullwidth_punctuation(self):
        assert edit("你好，世界\n你好（世界）\n", [("你好,世界\n你好(世界)\n", "你好，pi\n你好(pi)\n")]) == "你好，pi\n你好(pi)\n"

    def test_compatibility_forms(self):
        # Widths fold; a decomposed "e"+U+0301 does not, deliberately: models quote composed text and
        # supporting the decomposition would put a per-character table back on every edit.
        assert edit("ＡＢＣ１２３\ncafé\n", [("ABC123\ncafé\n", "XYZ789\ncoffee\n")]) == "XYZ789\ncoffee\n"
        with pytest.raises(EditError, match="Could not find"):
            edit("cafe\u0301\n", [("café", "coffee")])

    def test_smart_quotes(self):
        assert "world" in edit("console.log(\u2018hello\u2019);\n", [("console.log('hello');", "console.log('world');")])
        assert "Goodbye" in edit("const msg = \u201cHello World\u201d;\n", [('const msg = "Hello World";', 'const msg = "Goodbye";')])

    def test_unicode_dashes(self):
        assert "10-50" in edit("range: 1\u20135\nbreak\u2014here\n", [("range: 1-5\nbreak-here", "range: 10-50\nbreak--here")])

    def test_non_breaking_space(self):
        assert "universe" in edit("hello\u00a0world\n", [("hello world", "hello universe")])

    def test_exact_match_wins_over_fuzzy(self):
        assert edit("const x = 'exact';\n", [("const x = 'exact';", "const x = 'changed';")]) == "const x = 'changed';\n"

    def test_duplicates_are_detected_after_normalization(self):
        with pytest.raises(EditError, match="Found 2 occurrences"):
            edit("hello world   \nhello world\n", [("hello world", "replaced")])

    def test_untouched_lines_keep_their_bytes(self):
        original = "keep before  \nfirst target  \nfirst after\nkeep middle   \nsecond target  \nsecond after\nkeep after  \n"
        result = edit(
            original,
            [("first target\nfirst after", "FIRST\nFIRST2"), ("second target\nsecond after", "SECOND\nSECOND2")],
        )
        assert result == "keep before  \nFIRST\nFIRST2\nkeep middle   \nSECOND\nSECOND2\nkeep after  \n"

    def test_a_fuzzy_edit_does_not_rewrite_the_rest_of_the_line(self):
        """NFKC on the whole file would turn every full-width mark on the touched lines into half width."""
        original = "第一段：价格（含税）为１００元，“推荐”。\n第二段：保持不变（全角）。\n"
        result = edit("".join([original]), [('"推荐"', '"首选"'), ("第二段", "第2段")])
        assert result == '第一段：价格（含税）为１００元，"首选"。\n第2段：保持不变（全角）。\n'

    def test_a_fuzzy_edit_keeps_untouched_punctuation_inside_the_matched_line(self):
        original = "备注（内部）：请勿外传 — 老版本。\n"
        assert edit(original, [("备注(内部)", "备注(公开)")]) == "备注(公开)：请勿外传 — 老版本。\n"

    def test_overlapping_occurrences_are_refused(self):
        """``str.count`` is non-overlapping, so "}\n}\n" in "}\n}\n}\n" used to look unique."""
        with pytest.raises(EditError, match="Found 2 occurrences"):
            edit("}\n}\n}\n", [("}\n}\n", "X\n")])

    def test_quoted_trailing_spaces_are_consumed(self):
        """The old bug: the quoted space was dropped from the pattern, so the file's own space stayed behind."""
        assert edit('say "x" = 1\n', [("say “x” = ", "say “y” = ")]) == "say “y” = 1\n"

    def test_the_matched_occurrence_is_the_one_replaced(self):
        result = edit("replace me   \nafter   \n", [("replace me\n", "after\n")])
        assert result == "after\nafter   \n"


class TestLineEndings:
    def test_lf_old_text_matches_a_crlf_file(self):
        assert edit("first\r\nsecond\r\nthird\r\n", [("second\n", "REPLACED\n")]) == "first\r\nREPLACED\r\nthird\r\n"

    def test_lf_file_stays_lf(self):
        assert edit("first\nsecond\nthird\n", [("second\n", "REPLACED\n")]) == "first\nREPLACED\nthird\n"

    def test_duplicates_across_line_endings_are_detected(self):
        with pytest.raises(EditError, match="Found 2 occurrences"):
            edit("hello\r\nworld\r\n---\r\nhello\nworld\n", [("hello\nworld\n", "replaced\n")])

    def test_bom_survives(self):
        assert edit("\ufefffirst\r\nsecond\r\nthird\r\n", [("second\n", "REPLACED\n")]) == "\ufefffirst\r\nREPLACED\r\nthird\r\n"

    def test_bom_and_crlf_survive_multiple_edits(self):
        assert edit("\ufefffirst\r\nsecond\r\nthird\r\nfourth\r\n", [("second\n", "SECOND\n"), ("fourth\n", "FOURTH\n")]) == (
            "\ufefffirst\r\nSECOND\r\nthird\r\nFOURTH\r\n"
        )

    def test_inserted_lines_take_the_replaced_line_ending(self):
        assert edit("first\r\nsecond\r\nthird", [("second\nthird", "2a\r\n2b\nthird!")]) == "first\r\n2a\r\n2b\r\nthird!"
        assert edit("first\nsecond\nthird", [("second\nthird", "2a\r\n2b\nthird!")]) == "first\n2a\n2b\nthird!"

    def test_a_mixed_file_keeps_its_other_line_endings(self):
        assert edit("a\nb\r\nX = 1\r\n", [("X = 1", "X = 2")]) == "a\nb\r\nX = 2\r\n"
        assert edit("a\r\nb\nc", [("c", "C")]) == "a\r\nb\nC"

    def test_a_lone_cr_in_an_untouched_line_is_kept(self):
        assert edit("a\rb\nX = 1\n", [("X = 1", "X = 2")]) == "a\rb\nX = 2\n"

    def test_multiple_edits_take_their_own_line_endings(self):
        assert edit("lf1\nlf2\ncr1\r\ncr2\r\n", [("lf1\n", "A\nB\n"), ("cr1\n", "C\nD\n")]) == "A\nB\nlf2\nC\r\nD\r\ncr2\r\n"

    def test_fuzzy_widening_keeps_untouched_endings(self):
        assert edit("a\r\nfoo   \r\nb\n", [("foo\n", "FOO\n")]) == "a\r\nFOO\r\nb\n"

    def test_a_kept_lone_cr_does_not_merge_with_an_inserted_lf(self):
        assert edit("a\rb\nc", [("b", "\nB")]) == "a\r\n\nB\nc"
        assert edit("a\rb\nc", [("b", "")]) == "a\r\n\nc"

    def test_inserted_cr_does_not_merge_with_a_kept_lf(self):
        written = apply_replacements_to_original("a\rb\nc", [TextReplacement(2, 1, "\r")])
        assert normalize_to_lf(written) == "a\n\n\nc"


def test_every_short_mixed_ending_file_is_written_consistently():
    """Exhaustive over {a, \r, \n} up to length 6: the seam rule must hold everywhere, not just randomly."""
    alphabet = ["a", "\r", "\n"]
    for size in range(1, 7):
        for combination in itertools.product(alphabet, repeat=size):
            original = "".join(combination)
            normalized = normalize_to_lf(original)
            for start in range(len(normalized)):
                for length in range(1, len(normalized) - start + 1):
                    old_text = normalized[start : start + length]
                    for new_text in ("", "\n", "X\n", "\nY", "\rZ"):
                        try:
                            applied = apply_edits(normalized, [(old_text, new_text)], "/workspace/work/a.txt")
                        except EditError:
                            continue
                        written = apply_replacements_to_original(original, applied.replacements)
                        assert normalize_to_lf(written) == applied.new_content, (original, old_text, new_text, written)


def test_random_mixed_endings_always_normalize_to_the_diffed_content():
    """Pi's invariant over 3000 random files: the written file must equal the computed one when normalized."""
    endings = ["\n", "\r\n", "\r", ""]
    seed = 1

    def random(n: int) -> int:
        nonlocal seed
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        return (seed * n) // 0x80000000

    for _ in range(3000):
        original = "".join(f"w{random(100)}{'  ' if random(5) == 0 else ''}{endings[random(4)]}" for _ in range(random(12) + 1))
        normalized = normalize_to_lf(original)
        start = random(len(normalized))
        old_text = normalized[start : start + 1 + random(12)]
        new_text = ["", "\n", "X\n", "\nY", "\r", "Z"][random(6)]
        try:
            applied = apply_edits(normalized, [(old_text, new_text)], "/workspace/work/a.txt")
        except EditError:
            continue  # duplicate, not found or no change
        written = apply_replacements_to_original(original, applied.replacements)
        assert normalize_to_lf(written) == applied.new_content


def test_a_repeated_two_megabyte_file_is_searched_in_linear_time():
    """A regex of character classes backtracks here: 14.3 s for this exact input, holding the GIL throughout."""
    content = "a" * (2 * 1024 * 1024)
    started = time.perf_counter()
    with pytest.raises(EditError):
        edit_text(content, [("a" * 1000 + "b", "x")], "/workspace/work/a.txt")
    assert time.perf_counter() - started < 0.5


def test_a_csv_of_repeated_lines_edits_in_linear_time():
    rows = "".join(f"id,{index},alpha,beta,gamma\n" for index in range(120000))
    content = rows + "id,999999,价格（含税）为１００元\n"
    started = time.perf_counter()
    result = edit_text(content, [("id,999999,价格(含税)为100元\n", "id,999999,已改。\n")], "/workspace/work/a.csv")
    assert time.perf_counter() - started < 0.5
    assert result.endswith("id,999999,已改。\n") and result.count("已改") == 1


def test_the_worst_case_does_not_hold_the_gil():
    """``asyncio.to_thread`` cannot help if the search itself keeps the interpreter lock."""
    content = "a" * (2 * 1024 * 1024)
    ticks = 0
    done = threading.Event()

    def heartbeat() -> None:
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            time.sleep(0.001)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    time.sleep(0.02)  # let the heartbeat settle into its sleep cycle
    before = ticks
    with pytest.raises(EditError):
        edit_text(content, [("a" * 1000 + "b", "x")], "/workspace/work/a.txt")
    during = ticks - before
    done.set()
    thread.join(timeout=1)
    # A 14 s search lets this counter advance once; a linear one is over in milliseconds.
    assert during >= 2, during


def test_a_two_megabyte_chinese_edit_stays_inside_the_agent_memory_budget():
    """The per-character table this replaced peaked at 403 MiB for 5 MB; the tool limit is 2 MiB."""
    body = ""
    index = 0
    while len(body) < 2 * 1024 * 1024:
        body += f"第{index}段：价格（含税）为１００元，保持不变。\n"
        index += 1
    old_text = f"第{index - 1}段：价格(含税)为100元，保持不变。"  # half-width: forces the fuzzy path
    tracemalloc.start()
    result = edit_text(body, [(old_text, f"第{index - 1}段：已改。")], "/workspace/work/a.txt")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result.startswith("第0段：") and result.endswith(f"第{index - 1}段：已改。\n")
    assert peak < 32 * 1024 * 1024, peak
