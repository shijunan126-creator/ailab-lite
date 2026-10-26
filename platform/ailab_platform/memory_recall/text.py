"""Tokenizer and BM25 for the history search. Pure functions; no database, no model."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

_LATIN = re.compile(r"[a-z0-9]+(?:[_.\-/][a-z0-9]+)*")
_CJK = re.compile(r"[㐀-䶿一-鿿぀-ヿ가-힯]+")
_STOP = frozenset("a an and are as at be by for from has have in is it its of on or that the this to was were will with".split())

# Words that say "look it up" rather than say what to look up. Stripped from the *query* only.
_CUES = re.compile(
    "|".join(
        [
            "还记得|记得|上次|上回|之前|以前|先前|曾经|早些时候|前几天|前阵子|刚才|那次",
            "我们|咱们|我|你|讨论过|聊过|说过|提到过|提过|谈过|讨论|聊到|提到|问过|做过",
            "帮我|请问|一下|有关|关于|那个|这个|什么|怎么|是否|是不是|哪个|如何|结论|内容",
            "remember|recall|earlier|previously|before|last time|we|you|discussed|talked about|mentioned",
        ]
    ),
    re.I,
)

# Particles that end or join a question without being about anything. "是" only counts as one at the end of a phrase,
# because it also starts real words (是非, 是否).
_PARTICLES = re.compile(r"[的了吗呢吧啊呀嘛]|是(?=\s|$|[?？,，.。!！])")

K1 = 1.4
B = 0.75


def tokens(text: str, *, singles: bool = False) -> list[str]:
    """Latin words (identifiers also split on ``_ . - /``) and CJK bigrams.

    A lone CJK character is what is left of a phrase after the cues come out of a question ("聊" in "我们聊的周报");
    it matches half the history and says nothing, so it is dropped unless nothing else is left.
    """
    text = unicodedata.normalize("NFKC", text or "").casefold()
    out: list[str] = []
    for m in _LATIN.finditer(text):
        word = m.group()
        if word in _STOP or (len(word) < 2 and not word.isdigit()):
            continue
        out.append(word)
        parts = re.split(r"[_.\-/]", word)
        if len(parts) > 1:
            out.extend(p for p in parts if len(p) >= 2 and p not in _STOP)
    for run in _CJK.findall(text):
        if len(run) > 1:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
        elif singles:
            out.append(run)
    return out


def query_tokens(query: str) -> list[str]:
    cleaned = _PARTICLES.sub(" ", _CUES.sub(" ", unicodedata.normalize("NFKC", query or "")))
    found = tokens(cleaned) or tokens(cleaned, singles=True)
    return list(dict.fromkeys(found))


def identifier_like(token: str) -> bool:
    """Error codes, ids and function names: a match on one of these is rarely an accident."""
    return len(token) >= 4 and bool(re.search(r"[0-9_\-./]", token)) and bool(re.search(r"[a-z0-9]", token))


@dataclass(frozen=True)
class Doc:
    key: str
    weighted_tf: dict[str, float]
    length: float


def build_doc(key: str, *fields: tuple[str, float]) -> Doc:
    """``fields`` are (text, weight): the user's words count more than the assistant's."""
    tf: Counter = Counter()
    length = 0.0
    for text, weight in fields:
        toks = tokens(text)
        length += len(toks) * weight
        for tok, n in Counter(toks).items():
            tf[tok] += n * weight
    return Doc(key, dict(tf), length or 1.0)


@dataclass(frozen=True)
class Hit:
    key: str
    score: float
    coverage: float
    matched: tuple[str, ...]


def search(query: list[str], docs: list[Doc]) -> list[Hit]:
    """BM25 plus an IDF-weighted *coverage*: how much of what was asked this document answers.

    Coverage is what lets the caller abstain. BM25 alone always ranks something first.
    """
    if not query or not docs:
        return []
    n = len(docs)
    df = Counter(tok for d in docs for tok in d.weighted_tf)
    # A term nobody has said weighs like a term one document has: otherwise, in a small history,
    # a single unseen word outweighs everything that does match.
    idf = {t: math.log(1 + (n - max(df[t], 1) + 0.5) / (max(df[t], 1) + 0.5)) for t in query}
    total = sum(idf.values()) or 1.0
    avg_len = sum(d.length for d in docs) / n
    hits: list[Hit] = []
    for d in docs:
        matched = [t for t in query if t in d.weighted_tf]
        if not matched:
            continue
        score = 0.0
        for t in matched:
            tf = d.weighted_tf[t]
            score += idf[t] * tf * (K1 + 1) / (tf + K1 * (1 - B + B * d.length / avg_len))
        coverage = sum(idf[t] for t in matched) / total
        if any(identifier_like(t) for t in matched):
            coverage = max(coverage, 0.6)
        hits.append(Hit(d.key, score, coverage, tuple(matched)))
    return hits
