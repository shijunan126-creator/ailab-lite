"""Normalize file mentions and transactionally index current library heads."""

from __future__ import annotations

import itertools
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, event, inspect, literal_column, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .library import live_file_predicates
from .models import FileObject, FileSearch, FileSearchState, FileStatus
from .search_index import _fold, fold_with_map

_CJK = r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\U00020000-\U000323af"
_PIECES = re.compile(rf"([{_CJK}]+)|((?:(?![{_CJK}])[^\W_])+)")
_INDEXED_ATTRIBUTES = ("filename", "project_id", "is_head", "status", "purpose", "deleted_at", "purged_at")


@dataclass(frozen=True, slots=True)
class Fragment:
    value: str
    cjk: bool = False
    prefix: bool = False


def index_token(value: str, kind: str) -> str:
    # Both database parsers see exactly one ASCII term, even for casefold expansions and supplementary CJK.
    return kind + value.encode().hex()


def _word_spans(word: str) -> Iterator[tuple[int, int]]:
    """Keep the full identifier plus its camelCase, acronym and alphanumeric components."""
    yield 0, len(word)
    cuts = [0]
    for i in range(1, len(word)):
        left, right = word[i - 1], word[i]
        if (
            left.isdecimal() != right.isdecimal()
            or (right.isupper() and left.islower())
            or (right.isupper() and left.isupper() and i + 1 < len(word) and word[i + 1].islower())
        ):
            cuts.append(i)
    if len(cuts) == 1:
        return
    cuts.append(len(word))
    for i, (start, end) in enumerate(itertools.pairwise(cuts)):
        yield start, end
        if i + 2 < len(cuts) and not word[start].isdecimal() and word[end].isdecimal():
            yield start, cuts[i + 2]


def file_terms(filename: str, user_id: str) -> str:
    tokens = [index_token(user_id, "u")]
    for part in _PIECES.finditer(unicodedata.normalize("NFKC", filename)):
        word = part[0]
        if part[1]:
            folded = word.casefold()
            tokens.extend(index_token(char, "c") for char in folded)
            tokens.extend(index_token(folded[i : i + 2], "c") for i in range(len(folded) - 1))
        else:
            tokens.extend(index_token(word[start:end].casefold(), "l") for start, end in _word_spans(word))
    return " ".join(dict.fromkeys(tokens))


def query_fragments(query: str) -> list[Fragment]:
    normalized = unicodedata.normalize("NFKC", query)
    fragments = [Fragment(part[0].casefold(), cjk=bool(part[1])) for part in _PIECES.finditer(normalized)]
    for i in range(len(fragments) - 1, -1, -1):
        fragment = fragments[i]
        if not fragment.cjk:
            fragments[i] = Fragment(fragment.value, prefix=True)
            break
    return fragments


def query_tokens(fragments: list[Fragment], user_id: str) -> list[tuple[str, bool]]:
    tokens = [(index_token(user_id, "u"), False)]
    for fragment in fragments:
        word = fragment.value
        if fragment.cjk and len(word) > 1:
            tokens.extend((index_token(word[i : i + 2], "c"), False) for i in range(len(word) - 1))
        else:
            tokens.append((index_token(word, "c" if fragment.cjk else "l"), fragment.prefix))
    return list(dict.fromkeys(tokens))


def _occurrences(text: str, term: str) -> Iterator[tuple[int, int]]:
    start = text.find(term)
    while start >= 0:
        yield start, start + len(term)
        start = text.find(term, start + 1)


def match_name(filename: str, query: str, fragments: list[Fragment], *, fallback: bool) -> tuple[int, list[tuple[int, int]]] | None:
    folded = _fold(filename)
    if not fragments:
        hits = list(_occurrences(folded, query))
        return (2, hits) if hits else None
    if any(fragment.value not in folded for fragment in fragments):
        return None

    # Compute offsets in folded text, but retain case boundaries from the NFKC source.
    normalized = unicodedata.normalize("NFKC", filename)
    words: list[tuple[int, int]] = []
    cjk: list[tuple[int, int]] = []
    for part in _PIECES.finditer(normalized):
        word = part[0]
        base = part.start() if len(normalized) == len(folded) else len(normalized[: part.start()].casefold())
        if part[1]:
            cjk.append((base, base + len(word.casefold())))
        elif len(normalized) == len(folded):
            words.extend((base + a, base + b) for a, b in _word_spans(word))
        else:
            words.extend((base + len(word[:a].casefold()), base + len(word[:b].casefold())) for a, b in _word_spans(word))

    at_start = True
    used_fallback = False
    hits = []
    for fragment in fragments:
        value = fragment.value
        if fragment.cjk:
            # _occurrences gives local offsets inside each CJK run.
            matches = [
                (base + start, base + end, start == 0) for base, stop in cjk for start, end in _occurrences(folded[base:stop], value)
            ]
        else:
            matches = [
                (start, start + len(value), True)
                for start, end in words
                if (fragment.prefix or end - start == len(value)) and folded.startswith(value, start, end)
            ]
        if not matches and fallback and fragment.prefix:
            matches = [(start, end, False) for start, end in _occurrences(folded, value)]
            used_fallback = True
        if not matches:
            return None
        at_start &= any(is_start for _, _, is_start in matches)
        hits.extend((start, end) for start, end, _ in matches)

    stem, dot, _ = folded.rpartition(".")
    rank = 0 if folded.startswith(query) or (dot and stem == query) else 1 if at_start else 2
    return (2 if used_fallback else rank), hits


def highlight(filename: str, hits: list[tuple[int, int]]) -> list[tuple[int, int]]:
    _, positions = fold_with_map(filename)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(hits):
        start, end = positions[start][0], positions[end - 1][1]
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _index_values(file: FileObject) -> dict:
    return {
        "lineage_id": file.lineage_id,
        "file_id": file.id,
        "user_id": file.user_id,
        "project_id": file.project_id,
        "created_at": file.created_at,
        "terms": file_terms(file.filename, file.user_id),
    }


def _searchable(file: FileObject) -> bool:
    return (
        file.is_head == 1
        and file.purpose == "library"
        and file.status == FileStatus.READY
        and file.deleted_at is None
        and file.purged_at is None
    )


def mention_file_predicates():
    # Exact literal predicates let SQLite use the partial recency indexes.
    return FileObject.purpose == literal_column("'library'"), FileObject.status == literal_column("'ready'"), *live_file_predicates()


def _insert(connection, table):
    factory = sqlite_insert if connection.dialect.name == "sqlite" else pg_insert
    return factory(table)


def _sync_file(connection, file: FileObject) -> None:
    if _searchable(file):
        values = _index_values(file)
        stmt = _insert(connection, FileSearch).values(values)
        connection.execute(
            stmt.on_conflict_do_update(
                index_elements=[FileSearch.lineage_id],
                set_={key: getattr(stmt.excluded, key) for key in values if key != "lineage_id"},
            )
        )
    elif file.is_head or inspect(file).attrs.is_head.history.has_changes():
        connection.execute(delete(FileSearch).where(FileSearch.file_id == file.id))


@event.listens_for(FileObject, "after_insert")
def _index_new_file(_mapper, connection, file: FileObject) -> None:
    if _searchable(file):
        _sync_file(connection, file)


@event.listens_for(FileObject, "after_update")
def _index_changed_file(_mapper, connection, file: FileObject) -> None:
    state = inspect(file)
    if (file.is_head or state.attrs.is_head.history.has_changes()) and any(
        state.attrs[key].history.has_changes() for key in _INDEXED_ATTRIBUTES
    ):
        _sync_file(connection, file)


def index_pending(db: Session, batch_size: int = 200) -> bool:
    connection = db.connection()
    # Serialize watermark writers. On SQLite this also takes the writer lock before reading any names.
    state = _insert(connection, FileSearchState).values(
        id=1, watermark_user_id="", watermark_created_at=datetime(1970, 1, 1, tzinfo=UTC), watermark_file_id=""
    )
    connection.execute(state.on_conflict_do_nothing(index_elements=[FileSearchState.id]))
    watermark = db.scalar(select(FileSearchState).with_for_update())
    order = (FileObject.user_id, FileObject.created_at, FileObject.id)
    files = list(
        db.scalars(
            select(FileObject)
            .where(
                *mention_file_predicates(),
                tuple_(*order) > (watermark.watermark_user_id, watermark.watermark_created_at, watermark.watermark_file_id),
            )
            .order_by(*order)
            .limit(batch_size)
            .with_for_update(nowait=True)
        )
    )
    rows = [_index_values(file) for file in files]
    if rows:
        stmt = _insert(connection, FileSearch)
        connection.execute(stmt.on_conflict_do_nothing(index_elements=[FileSearch.lineage_id]), rows)
    if files:
        watermark.watermark_user_id, watermark.watermark_created_at, watermark.watermark_file_id = (
            files[-1].user_id,
            files[-1].created_at,
            files[-1].id,
        )
    db.commit()
    return len(files) == batch_size
