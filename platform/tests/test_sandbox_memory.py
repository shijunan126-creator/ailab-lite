import pytest

from ailab_platform.sandbox_tools.sandbox_memory import retained_bytes


def test_memory_excludes_clean_file_mappings_but_counts_anonymous_dirty_and_swapped_pages(tmp_path):
    (tmp_path / "smaps").write_text(
        "Rss: 400000 kB\nAnonymous: 0 kB\nPrivate_Dirty: 0 kB\nShared_Dirty: 0 kB\nSwap: 0 kB\nVmFlags: rd ex\n"
        "Rss: 120 kB\nAnonymous: 100 kB\nPrivate_Dirty: 100 kB\nShared_Dirty: 0 kB\nSwap: 20 kB\nVmFlags: rd wr\n"
        "Rss: 80 kB\nAnonymous: 0 kB\nPrivate_Dirty: 40 kB\nShared_Dirty: 40 kB\nSwap: 0 kB\nVmFlags: rd wr\n"
    )
    assert retained_bytes(tmp_path) == 200 * 1024


def test_memory_fails_closed_when_unreadable_or_incomplete(tmp_path):
    with pytest.raises(OSError):
        retained_bytes(tmp_path)
    (tmp_path / "smaps").write_text("Anonymous: 100 kB\n")
    with pytest.raises(RuntimeError, match="invalid_memory_accounting"):
        retained_bytes(tmp_path)
