"""Query-relevant excerpts from fetched page text, deterministic and CPU-only.

A search result's snippet is often enough to the model and the page body is not: reading every hit
costs a model round trip and puts unrelated navigation into the context. The Agent fetches the top
hits and keeps only the paragraphs that share the query's terms, in the page's own order.
"""

from __future__ import annotations

import re
from itertools import pairwise

EXCERPT_CHARS = 800
FULL_TEXT_CHARS = 2000

_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_WORD = re.compile(r"[a-z0-9]{2,}")
_PARAGRAPH = re.compile(r"\n\s*\n")


def terms(query: str) -> set[str]:
    """English by word; Chinese by adjacent-character bigrams, which do not need a word segmenter."""
    lowered = query.lower()
    found = set(_WORD.findall(lowered))
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            found.add(run)
        else:
            found.update(first + second for first, second in pairwise(run))
    return found


def _score(paragraph: str, wanted: set[str]) -> int:
    lowered = paragraph.lower()
    return sum(lowered.count(term) for term in wanted)


def excerpt(text: str, query: str, max_chars: int = EXCERPT_CHARS) -> str:
    """The highest-scoring paragraphs of ``text`` for ``query``, joined in their original order.

    An empty string means nothing in the page matched the query; the search hit's own snippet is
    then the better text to keep, and the model can still read_url it.
    """
    if not text or max_chars <= 0:
        return ""
    wanted = terms(query)
    if not wanted:
        return ""
    paragraphs = [part.strip() for part in _PARAGRAPH.split(text) if part.strip()]
    ranked = sorted((-_score(part, wanted), index) for index, part in enumerate(paragraphs))
    picked: list[tuple[int, str]] = []
    used = 0
    for negative, index in ranked:
        if negative == 0:
            break
        separator = 2 if picked else 0  # the blank line between paragraphs in the joined result
        room = max_chars - used - separator
        if room <= 0:
            break
        part = paragraphs[index]
        if len(part) > room:
            part = part[:room]
        picked.append((index, part))
        used += len(part) + separator
    if not picked:
        return ""
    text_out = "\n\n".join(part for _, part in sorted(picked))
    return text_out.strip()
