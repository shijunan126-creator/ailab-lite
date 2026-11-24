"""Bounded filename suggestions and conversation scope for the public files API."""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import and_, case, column, false, func, literal_column, or_, select, table, true
from sqlalchemy.orm import Session, aliased

from ..file_search import highlight, match_name, mention_file_predicates, query_fragments, query_tokens
from ..models import Artifact, Conversation, FileObject, FileSearch, Project, Run
from ..search_index import _fold
from .serializers import file_dicts

INDEX_CANDIDATES = 200
CONVERSATION_CANDIDATES = 50
RECENT_CANDIDATES = 2000
INDEX_WINDOW = 1024

_FTS = table("file_search_fts", column("rowid"))
_ORIGINS = ("conversation", "project", "library")


def _conversation_lineages(dialect: str, user_id: str, conversation_id: str):
    # Run input JSON also covers runs created before message_file_refs existed, including inactive branches.
    runs = (
        select(Run.id, Run.user_id, Run.input_file_ids)
        .where(Run.conversation_id == conversation_id)
        .cte("mention_runs")
        .prefix_with("MATERIALIZED")
    )
    if dialect == "sqlite":
        inputs = func.json_each(runs.c.input_file_ids).table_valued("value")
    else:
        array = case((func.json_typeof(runs.c.input_file_ids) == "array", runs.c.input_file_ids), else_=literal_column("'[]'::json"))
        inputs = func.json_array_elements_text(array).table_valued("value")
    referenced = (
        select(inputs.c.value.label("file_id"))
        .join_from(runs, inputs, true())
        .where(runs.c.user_id == user_id)
        .union(select(Artifact.file_id).join(runs, runs.c.id == Artifact.run_id).where(runs.c.user_id == user_id))
        .subquery()
    )
    # Resolve each checked reference by primary key. PostgreSQL overestimates rows from JSON expansion and can
    # otherwise hash-join the conversation's handful of references against every file in the account.
    lineage = (
        select(case((FileObject.user_id == user_id, FileObject.lineage_id)))
        .where(FileObject.id == referenced.c.file_id)
        .correlate(referenced)
        .scalar_subquery()
    )
    return select(lineage.label("lineage_id")).select_from(referenced).distinct().cte("conversation_lineages")


def _scope(source, user_id, project_id, isolated, references):
    if isolated:
        allowed = source.project_id == project_id
    else:
        isolated_projects = select(Project.id).where(Project.user_id == user_id, Project.memory_mode == "isolated")
        allowed = or_(source.project_id.is_(None), source.project_id.not_in(isolated_projects))
    in_conversation = source.lineage_id.in_(select(references.c.lineage_id)) if references is not None else false()
    return or_(allowed, in_conversation), in_conversation


def _available(user_id):
    return FileObject.user_id == user_id, *mention_file_predicates()


def _index_window(db, user_id):
    newest = (
        select(FileSearch.id, FileSearch.created_at, FileSearch.file_id)
        .where(FileSearch.user_id == user_id)
        .order_by(FileSearch.created_at.desc(), FileSearch.file_id)
        .limit(INDEX_WINDOW)
        .subquery()
    )
    window = db.execute(
        select(
            func.min(newest.c.id).over().label("lo"),
            func.max(newest.c.id).over().label("hi"),
            func.count().over().label("size"),
            newest.c.created_at,
            newest.c.file_id,
        )
        .order_by(newest.c.created_at, newest.c.file_id.desc())
        .limit(1)
    ).one_or_none()
    # Row IDs cluster as heads are created but are independent of recency. Only use a narrow range, then enforce
    # the actual time and file-ID boundary before accepting a complete newest page.
    return window if window is not None and window.size == INDEX_WINDOW and window.hi - window.lo < INDEX_WINDOW * 4 else None


def candidate_query(
    dialect: str, user_id: str, tokens: list[tuple[str, bool]], project_id=None, isolated=False, references=None, *, window=None
):
    source = FileSearch
    if dialect == "postgresql":
        match = " & ".join(term + (":*" if prefix else "") for term, prefix in tokens)
        tsquery = func.to_tsquery(literal_column("'pg_catalog.simple'"), match)
        full_text_match = FileSearch.terms_tsv.op("@@")(tsquery)
        if window is None:
            # Give the GIN predicate its own scan before sorting or testing the duplicated relational scope.
            source = (
                select(FileSearch.file_id, FileSearch.lineage_id, FileSearch.user_id, FileSearch.project_id, FileSearch.created_at)
                .where(full_text_match)
                .cte("matched")
                .prefix_with("MATERIALIZED", dialect="postgresql")
                .c
            )
    allowed, in_conversation = _scope(source, user_id, project_id, isolated, references)
    ranked = select(
        source.file_id,
        source.created_at,
        in_conversation.label("in_conversation"),
    ).where(source.user_id == user_id, allowed)
    if window is not None:
        ranked = ranked.where(
            or_(
                FileSearch.created_at > window.created_at,
                and_(FileSearch.created_at == window.created_at, FileSearch.file_id <= window.file_id),
            )
        )
    if dialect == "sqlite":
        match = " AND ".join(f'"{term}"' + ("*" if prefix else "") for term, prefix in tokens)
        matches = select(_FTS.c.rowid.label("id")).where(literal_column("file_search_fts").match(match)).order_by(_FTS.c.rowid.desc())
        if window is not None:
            matches = matches.where(_FTS.c.rowid.between(window.lo, window.hi))
        matches = matches.cte("matched").prefix_with("MATERIALIZED", dialect="sqlite")
        ranked = ranked.join_from(matches, FileSearch, FileSearch.id == matches.c.id)
    elif window is not None:
        ranked = ranked.where(full_text_match, FileSearch.id.between(window.lo, window.hi))
    candidates = ranked.order_by(source.created_at.desc(), source.file_id).limit(INDEX_CANDIDATES).subquery()
    return select(FileObject.id, FileObject.filename, FileObject.project_id, candidates.c.created_at, candidates.c.in_conversation).join(
        candidates, candidates.c.file_id == FileObject.id
    )


def conversation_query(user_id, references):
    head = (
        select(FileObject.id)
        .where(FileObject.lineage_id == references.c.lineage_id, FileObject.is_head == literal_column("1"))
        .correlate(references)
        .scalar_subquery()
    )
    # Drive these lookups from the conversation, even when the account has a larger recency index.
    # Unavailable heads sort last and are ignored by the caller.
    return (
        select(FileObject.id, FileObject.filename, FileObject.project_id, FileObject.created_at, true().label("in_conversation"))
        .join_from(references, FileObject, and_(FileObject.id == head, *_available(user_id)), isouter=True)
        .order_by(FileObject.created_at.desc().nulls_last(), FileObject.id)
        .limit(CONVERSATION_CANDIDATES)
    )


def recent_query(user_id, project_id=None, isolated=False, references=None, *, limit, project_only=False):
    allowed, in_conversation = _scope(FileObject, user_id, project_id, isolated, references)
    query = select(
        FileObject.id, FileObject.filename, FileObject.project_id, FileObject.created_at, in_conversation.label("in_conversation")
    )
    query = query.where(*_available(user_id))
    if project_only or (isolated and references is None):
        query = query.where(FileObject.project_id == project_id)
    else:
        query = query.where(allowed)
    return query.order_by(FileObject.created_at.desc(), FileObject.id).limit(limit)


def suggestions(db: Session, user_id: str, query: str, limit: int, conversation_id=None, project_id=None, *, isolated=False) -> list[dict]:
    dialect = db.get_bind().dialect.name
    references = _conversation_lineages(dialect, user_id, conversation_id) if conversation_id is not None else None
    query = query.strip()
    fragments = query_fragments(query)
    query = _fold(query)
    ranked: dict = {}

    def collect(rows, *, fallback=False):
        for row in rows:
            if row.id is None or row.id in ranked:
                continue
            match = match_name(row.filename, query, fragments, fallback=fallback) if query else (0, [])
            if match is not None:
                rank, hits = match
                origin = 0 if row.in_conversation else 1 if project_id is not None and row.project_id == project_id else 2
                ranked[row.id] = (
                    (2 if fallback else rank, origin, -row.created_at.replace(tzinfo=UTC).timestamp(), row.id),
                    hits,
                    row.filename,
                )

    if references is not None:
        collect(db.execute(conversation_query(user_id, references)))

    if query:
        if fragments:
            tokens = query_tokens(fragments, user_id)
            args = (dialect, user_id, tokens, project_id, isolated, references)
            indexed = []
            if (window := _index_window(db, user_id)) is not None:
                indexed = db.execute(candidate_query(*args, window=window)).all()
            if len(indexed) < INDEX_CANDIDATES:
                indexed = db.execute(candidate_query(*args)).all()
            collect(indexed)
        if len(ranked) < limit:
            collect(
                db.execute(recent_query(user_id, project_id, isolated, references, limit=RECENT_CANDIDATES)),
                fallback=True,
            )
    else:
        if project_id is not None and len(ranked) < limit:
            collect(db.execute(recent_query(user_id, project_id, limit=limit, project_only=True)))
        if not isolated and len(ranked) < limit:
            collect(db.execute(recent_query(user_id, limit=limit)))

    ids = sorted(ranked, key=lambda file_id: ranked[file_id][0])[:limit]
    if not ids:
        return []
    rows = {
        file.id: (file, delivered, title, name)
        for file, delivered, title, name in _details(db, ids, user_id, project_id, isolated, references)
    }
    ordered = [rows[file_id] for file_id in ids if file_id in rows]
    files = file_dicts(db, [row[0] for row in ordered])
    return [
        {
            **file_out,
            "origin": _ORIGINS[ranked[file.id][0][1]],
            "delivered": bool(artifact_id),
            "conversation_title": title or None,
            "project_name": name,
            "highlight": highlight(file.filename, ranked[file.id][1]) if file.filename == ranked[file.id][2] else [],
        }
        for (file, artifact_id, title, name), file_out in zip(ordered, files, strict=True)
    ]


def _details(db, ids, user_id, project_id, isolated, references):
    source_file = aliased(FileObject)
    source_run = aliased(Run)
    # Prefer the head's delivery, then an earlier version's delivery. Fork copies have a later artifact creation time.
    artifact = (
        select(Artifact.id)
        .join_from(Artifact, source_file, source_file.id == Artifact.file_id)
        .join_from(Artifact, source_run, source_run.id == Artifact.run_id)
        .where(source_file.lineage_id == FileObject.lineage_id, source_file.user_id == user_id, source_run.user_id == user_id)
        .order_by(source_file.version.desc(), Artifact.created_at, Artifact.id)
        .correlate(FileObject)
        .limit(1)
        .scalar_subquery()
    )
    allowed, _ = _scope(FileObject, user_id, project_id, isolated, references)
    return db.execute(
        select(FileObject, Artifact.id, Conversation.title, Project.name)
        .join_from(FileObject, Artifact, Artifact.id == artifact, isouter=True)
        .join_from(Artifact, Run, Run.id == Artifact.run_id, isouter=True)
        .join_from(
            Run,
            Conversation,
            and_(Conversation.id == Run.conversation_id, Conversation.user_id == user_id, Conversation.deleted_at.is_(None)),
            isouter=True,
        )
        .join_from(FileObject, Project, Project.id == FileObject.project_id, isouter=True)
        .where(FileObject.id.in_(ids), *_available(user_id), allowed)
    )
