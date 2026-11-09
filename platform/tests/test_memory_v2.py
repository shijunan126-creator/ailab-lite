"""Synthetic regressions for memory v2; never contains user history."""

from ailab_platform.memory_recall.text import query_tokens
from ailab_platform.memory_dream.service import _assistant_context


def test_query_cues_preserve_word_boundaries():
    for word in ("weekly", "power", "youtube", "answer"):
        assert word in query_tokens(word)
    assert "我国" in query_tokens("我国经济")
    assert "report" in query_tokens("we discussed weekly report")
    assert "we" not in query_tokens("we discussed weekly report")


def test_extraction_context_is_short_and_has_no_code_or_images():
    assert _assistant_context("说明\n```openui\nsecret_dsl\n```\n![图](image_123)\n结束") == "说明\n\n\n结束"
    assert len(_assistant_context("长" * 1000)) == 300
