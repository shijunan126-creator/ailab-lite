"""Line windows and truncation, matching Pi's ``read`` and ``truncate`` behaviour."""

from __future__ import annotations

import pytest

from ailab_platform.domain.text_truncate import (
    DEFAULT_MAX_BYTES,
    format_size,
    read_lines,
    truncate_head,
    truncate_line,
    truncate_tail,
)


def test_content_that_fits_is_returned_unchanged():
    window = read_lines("Hello, world!\nLine 2\nLine 3", "/workspace/a.txt")
    assert window.text == "Hello, world!\nLine 2\nLine 3"
    assert "Use offset=" not in window.text
    assert window.next_offset is None and window.truncation.truncated is False
    assert window.total_lines == 3


def test_line_limit_truncates_with_a_continuation_notice():
    text = "\n".join(f"Line {index}" for index in range(1, 2501))
    window = read_lines(text, "/workspace/a.txt")
    assert "Line 1" in window.text and "Line 2000" in window.text
    assert "Line 2001" not in window.text
    assert window.text.endswith("[Showing lines 1-2000 of 2500. Use offset=2001 to continue.]")
    assert (window.truncation.truncated_by, window.truncation.output_lines) == ("lines", 2000)


def test_byte_limit_truncates_with_a_byte_notice():
    text = "\n".join(f"Line {index}: {'x' * 200}" for index in range(1, 501))
    window = read_lines(text, "/workspace/a.txt")
    assert window.truncation.truncated_by == "bytes"
    assert window.text.count("\n\n[Showing lines 1-") == 1
    assert "(50.0KB limit). Use offset=" in window.text
    assert len(window.text.encode()) < 60_000


def test_offset_starts_at_one():
    text = "\n".join(f"Line {index}" for index in range(1, 101))
    window = read_lines(text, "/workspace/a.txt", offset=51)
    assert "Line 50" not in window.text and "Line 51" in window.text and "Line 100" in window.text
    assert "Use offset=" not in window.text


def test_limit_reports_the_lines_left():
    text = "\n".join(f"Line {index}" for index in range(1, 101))
    launched = read_lines(text, "/workspace/a.txt", limit=10)
    assert "Line 10" in launched.text and "Line 11" not in launched.text
    assert launched.text.endswith("[90 more lines in file. Use offset=11 to continue.]")
    combined = read_lines(text, "/workspace/a.txt", offset=41, limit=20)
    assert "Line 60" in combined.text and "Line 61" not in combined.text
    assert combined.text.endswith("[40 more lines in file. Use offset=61 to continue.]")


def test_offset_beyond_the_end_names_both_counts():
    with pytest.raises(ValueError, match=r"Offset 100 is beyond end of file \(3 lines total\)"):
        read_lines("Line 1\nLine 2\nLine 3", "/workspace/a.txt", offset=100)


def test_a_single_line_over_the_byte_cap_points_at_bash():
    window = read_lines("x" * (DEFAULT_MAX_BYTES + 1), "/workspace/work/big.txt")
    assert window.next_offset is None
    assert window.text.startswith("[Line 1 is ")
    assert "exceeds 50.0KB limit" in window.text
    assert "sed -n '1p' /workspace/work/big.txt | head -c 51200" in window.text


def test_head_truncation_never_splits_a_line():
    truncation = truncate_head("a" * 100 + "\n" + "b" * 100, max_bytes=150)
    assert truncation.content == "a" * 100
    assert truncation.truncated_by == "bytes"


def test_tail_truncation_keeps_the_end():
    truncation = truncate_tail("\n".join(f"line {index}" for index in range(1, 11)), max_lines=3)
    assert truncation.content == "line 8\nline 9\nline 10"
    assert truncation.truncated_by == "lines"


def test_tail_truncation_keeps_a_partial_last_line_on_a_character_boundary():
    truncation = truncate_tail("前言\n" + "尾" * 20, max_bytes=13)
    assert truncation.last_line_partial is True
    assert truncation.content.endswith("尾" * 4)  # 12 bytes of 3-byte characters, never half of one


def test_truncate_line_marks_the_cut():
    assert truncate_line("short") == ("short", False)
    text, cut = truncate_line("y" * 600)
    assert cut is True and text.endswith("... [truncated]") and len(text) == 500 + len("... [truncated]")


def test_format_size_rounds_halves_up():
    assert format_size(512) == "512B"
    assert format_size(1536) == "1.5KB"
    assert format_size(1280) == "1.3KB"  # toFixed(1) rounds 1.25 up; Python's format() would give 1.2
    assert format_size(DEFAULT_MAX_BYTES) == "50.0KB"
    assert format_size(3 * 1024 * 1024) == "3.0MB"
