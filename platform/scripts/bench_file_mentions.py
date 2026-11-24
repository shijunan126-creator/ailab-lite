"""Measure file mention suggestions on a disposable database with 100k owned files.

Run from platform: python scripts/bench_file_mentions.py
For PostgreSQL, pass --database-url pointing to a fresh database ending in _bench.
"""

from __future__ import annotations

import argparse
import math
import os
import resource
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import insert, inspect, select, text
from sqlalchemy.engine import make_url


def seed(db, owned, interference):
    from ailab_platform.file_search import file_terms
    from ailab_platform.models import Artifact, Conversation, FileObject, FileSearch, Project, Run, User

    owner = db.scalar(select(User))
    other = User(id="usr_mention_distractor", email="distractor@example.invalid", display_name="Distractor")
    shared = Project(id="prj_mention_shared", user_id=owner.id, name="Shared")
    isolated = Project(id="prj_mention_isolated", user_id=owner.id, name="Isolated", memory_mode="isolated")
    db.add(other)
    db.add_all([shared, isolated])
    db.commit()

    start = datetime(2026, 1, 1, tzinfo=UTC)
    files = []
    for i in range(owned + interference):
        user = owner.id if i < owned else other.id
        filename = (
            f"QuarterlyReport_{i}.pptx"
            if i % 10 < 3
            else f"财务台账_{i}.xlsx"
            if i % 10 < 6
            else f"RoadmapDraft_{i}.docx"
            if i % 10 < 9
            else f"执行记录_{i}.pdf"
        )
        if i in {7, 8, 9} or (i >= owned and i % 500 == 7):
            filename = f"ZephyrArchive_{i}.zip"
        file_id = f"fil_bench{i:08d}"
        project_id = None
        if user == owner.id:
            project_id = {0: isolated.id, 1: shared.id}.get(i % 11)
        files.append(
            dict(
                id=file_id,
                user_id=user,
                lineage_id=file_id,
                project_id=project_id,
                filename=filename,
                storage_key=file_id,
                status="ready",
                created_at=start + timedelta(seconds=i),
            )
        )
    for batch in range(0, len(files), 1000):
        db.execute(insert(FileObject), files[batch : batch + 1000])
    conversation = Conversation(user_id=owner.id, project_id=shared.id, title="Quarterly planning")
    db.add(conversation)
    db.flush()
    runs = [Run(user_id=owner.id, conversation_id=conversation.id, prompt="Plan", input_file_ids=[files[i % 4]["id"]]) for i in range(12)]
    db.add_all(runs)
    db.flush()
    db.add(Artifact(run_id=runs[-1].id, file_id=files[6]["id"]))
    db.commit()
    before = index_size(db)
    started = time.perf_counter()
    for batch in range(0, len(files), 1000):
        rows = [
            {
                **{key: file[key] for key in ("lineage_id", "user_id", "project_id", "created_at")},
                "file_id": file["id"],
                "terms": file_terms(file["filename"], file["user_id"]),
            }
            for file in files[batch : batch + 1000]
        ]
        db.execute(insert(FileSearch), rows)
    db.commit()
    seconds = time.perf_counter() - started
    after = index_size(db)
    db.execute(text("ANALYZE"))
    db.commit()
    print(f"Index build {seconds:.2f} s, full-text tables and indexes {(after - before) / (1024**2):.2f} MiB")
    return owner.id, shared.id, conversation.id


def index_size(db):
    if db.get_bind().dialect.name == "sqlite":
        return db.scalar(text("PRAGMA page_size")) * db.scalar(text("PRAGMA page_count"))
    return db.scalar(text("SELECT pg_total_relation_size('file_search')"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url")
    parser.add_argument("--files", type=int, default=100_000)
    parser.add_argument("--interference", type=int, default=25_000)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--explain", action="store_true")
    args = parser.parse_args()
    if args.files < 10 or args.interference < 0 or args.iterations < 1:
        parser.error("use at least 10 owned files, 0 interference files and 1 sample per query")
    with tempfile.TemporaryDirectory(prefix="bench-file-mentions-") as temp:
        url = make_url(args.database_url or f"sqlite:///{temp}/mentions.db")
        if url.get_backend_name() == "postgresql" and not (url.database or "").endswith("_bench"):
            parser.error("use a new, disposable PostgreSQL database ending in _bench")
        os.environ.update(
            AILAB_DATABASE_URL=url.render_as_string(hide_password=False),
            AILAB_ENV="test",
            AILAB_ENV_FILE=str(Path(temp) / "no.env"),
            AILAB_DATA_DIR=temp,
        )
        from ailab_platform.api.file_mentions import candidate_query, suggestions
        from ailab_platform.db import SessionLocal, engine, init_db
        from ailab_platform.file_search import query_fragments, query_tokens

        if inspect(engine).has_table("files"):
            parser.error("the benchmark database must be empty")
        init_db()
        with SessionLocal() as db:
            owner, project, conversation = seed(db, args.files, args.interference)
        dialect = url.get_backend_name()
        print(f"{dialect}: {args.files:,} owned + {args.interference:,} other files, {args.iterations} samples/query")
        print(f"{'query':<18} {'p50 ms':>9} {'p95 ms':>9} {'results':>8}")
        all_passed = True
        for label, query in (
            ("empty", ""),
            ("one Latin", "q"),
            ("one CJK", "财"),
            ("common (30%)", "report"),
            ("rare", "zephyr"),
            ("two fragments", "quarterly ppt"),
        ):
            with SessionLocal() as db:
                suggestions(db, owner, query, 8, conversation_id=conversation, project_id=project)
            durations = []
            for _ in range(args.iterations):
                start = time.perf_counter_ns()
                with SessionLocal() as db:
                    results = suggestions(db, owner, query, 8, conversation_id=conversation, project_id=project)
                durations.append((time.perf_counter_ns() - start) / 1e6)
            durations.sort()
            p95 = durations[math.ceil(len(durations) * 0.95) - 1]
            all_passed &= p95 <= 25
            print(f"{label:<18} {durations[len(durations) // 2]:9.2f} {p95:9.2f} {len(results):8}")
            if args.explain and query:
                with SessionLocal() as db:
                    sql = candidate_query(dialect, owner, query_tokens(query_fragments(query), owner), project)
                    explain = "EXPLAIN QUERY PLAN " if dialect == "sqlite" else "EXPLAIN (ANALYZE, BUFFERS) "
                    for row in db.execute(text(explain + str(sql.compile(db.get_bind(), compile_kwargs={"literal_binds": True})))):
                        print(*row)
        print(f"Process max RSS: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss} (system units)")
        return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
