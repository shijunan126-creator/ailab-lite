"""Compare the indexed whole-account search with the old bounded scan on synthetic data.

Run from ``platform`` with the project virtualenv::

    python scripts/bench_search.py --conversations 2000 --runs-per-conversation 20
    python scripts/bench_search.py --database-url postgresql+psycopg://postgres@127.0.0.1:15432/ailab_bench --reset

The legacy path below is a frozen copy of the pre-index implementation (cursor loop included), so the two columns measure
the same queries and the same result limit. This is a benchmark, not a product execution path; never point it at a
developer or production database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

LEGACY_CONVERSATION_BATCH = 50
LEGACY_RUN_BATCH = 100
LEGACY_RUN_BUDGET = 1000
LEGACY_MAX_PAGES = 500

WORDS = [
    "模型",
    "训练",
    "部署",
    "评估",
    "数据",
    "分析",
    "计划",
    "预算",
    "项目",
    "搜索",
    "性能",
    "优化",
    "接口",
    "测试",
    "文档",
    "团队",
    "会议",
    "结论",
    "方案",
    "服务",
    "记录",
    "问题",
    "回答",
    "时间",
    "版本",
]
ENGLISH = ["deploy", "service", "ailab_platform", "release", "search", "index", "review", "performance"]
RARE = "稀世搜索标记XYZ"
TITLE_NEEDLE = "标题唯一标记ABC"


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversations", type=int, default=2000)
    parser.add_argument("--runs-per-conversation", type=int, default=20)
    parser.add_argument("--text-chars", type=int, default=1200, help="average answer length")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--reset", action="store_true", help="drop the throwaway database before seeding")
    return parser.parse_args()


def _configure_database(args: argparse.Namespace, scratch: Path) -> str:
    if args.database_url:
        url = args.database_url
        if args.reset:
            from sqlalchemy import create_engine, text
            from sqlalchemy.engine import make_url

            parsed = make_url(url)
            if parsed.get_backend_name() == "sqlite":
                path = parsed.database
                if path and os.path.exists(path):
                    os.unlink(path)
            elif parsed.get_backend_name() == "postgresql":
                if not (parsed.database or "").endswith(("_bench", "_test")):
                    raise SystemExit("refusing to reset a PostgreSQL database whose name does not end in _bench or _test")
                engine = create_engine(url, isolation_level="AUTOCOMMIT")
                with engine.connect() as conn:
                    conn.execute(text("DROP SCHEMA public CASCADE"))
                    conn.execute(text("CREATE SCHEMA public"))
                engine.dispose()
            else:
                raise SystemExit(f"unsupported benchmark database: {parsed.get_backend_name()}")
    else:
        url = f"sqlite:///{scratch / 'bench.db'}"
    os.environ["AILAB_DATABASE_URL"] = url
    os.environ.setdefault("AILAB_ENV", "test")
    os.environ.setdefault("AILAB_ENV_FILE", os.devnull)
    os.environ.setdefault("AILAB_DATA_DIR", str(scratch / "data"))
    return url


def _text(rng: random.Random, length: int) -> str:
    parts: list[str] = []
    used = 0
    while used < length:
        if rng.random() < 0.08:
            chunk = rng.choice(ENGLISH) + " "
        else:
            chunk = "".join(rng.choice(WORDS) for _ in range(rng.randint(6, 16))) + "。"
        parts.append(chunk)
        used += len(chunk)
    return "".join(parts)[:length]


def _seed(db, conversations: int, runs_per_conversation: int, text_chars: int) -> tuple[str, str]:
    from sqlalchemy import insert

    from ailab_platform.models import Conversation, Run, User

    rng = random.Random(20261111)
    now = datetime.now(UTC).replace(tzinfo=None)
    base = now - timedelta(days=conversations + 2)
    user = db.query(User).first()
    if user is None:
        raise SystemExit("run the API once so the development user exists, or seed a user first")
    conversation_rows = []
    run_rows = []
    for index in range(conversations):
        created = base + timedelta(days=index)
        title = _text(rng, 24)
        if index == 7:
            title = f"季度计划 {TITLE_NEEDLE}"
        conversation_rows.append(
            {
                "id": f"cnv_bench{index:06d}",
                "user_id": user.id,
                "title": title,
                "model": "bench",
                "created_at": created,
                "updated_at": created + timedelta(minutes=runs_per_conversation + 1),
                "memory_policy": {},
            }
        )
        for turn in range(runs_per_conversation):
            prompt = _text(rng, 160)
            summary = _text(rng, text_chars)
            if index == 0 and turn == 0:
                summary = summary[:600] + RARE + summary[600:]
            run_rows.append(
                {
                    "id": f"run_bench{index:06d}{turn:04d}",
                    "user_id": user.id,
                    "conversation_id": f"cnv_bench{index:06d}",
                    "title": "",
                    "prompt": prompt,
                    "status": "succeeded",
                    "model": "bench",
                    "input_file_ids": [],
                    "options": {},
                    "runtime_meta": {},
                    "result": {},
                    "summary": summary,
                    "error": "",
                    "context_source_epoch": 0,
                    "checkpoint_step": 0,
                    "work_ms": 0,
                    "created_at": created + timedelta(minutes=turn),
                    "updated_at": created + timedelta(minutes=turn),
                }
            )
    for start in range(0, len(conversation_rows), 500):
        db.execute(insert(Conversation), conversation_rows[start : start + 500])
    for start in range(0, len(run_rows), 500):
        db.execute(insert(Run), run_rows[start : start + 500])
    db.commit()
    return user.id, RARE


def _legacy_snippet(text, needle):
    folded, positions = [], []
    for index, char in enumerate(text):
        part = char.casefold()
        folded.append(part)
        positions.extend([index] * len(part))
    position = "".join(folded).find(needle.casefold())
    if position < 0:
        return text[:120], []
    start, end = positions[position], positions[position + len(needle.casefold()) - 1] + 1
    left = max(0, start - 40)
    value = text[left : left + 120]
    return value, [[start - left, min(end - left, len(value))]]


def _legacy_conversation_scan(user_id, before=None):
    from sqlalchemy import select, tuple_

    from ailab_platform.models import Conversation

    query = select(Conversation).where(Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
    if before:
        query = query.where(tuple_(Conversation.updated_at, Conversation.id) < (datetime.fromisoformat(before[0]), before[1]))
    return query.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(LEGACY_CONVERSATION_BATCH + 1)


def _legacy_run_scan(conversation_id, before=None):
    from sqlalchemy import select, tuple_

    from ailab_platform.models import Run

    query = select(Run.id, Run.prompt, Run.summary, Run.created_at).where(Run.conversation_id == conversation_id)
    if before:
        query = query.where(tuple_(Run.created_at, Run.id) < (datetime.fromisoformat(before[0]), before[1]))
    return query.order_by(Run.created_at.desc(), Run.id.desc()).limit(LEGACY_RUN_BATCH).subquery("search_window")


def _legacy_match_query(window, q, dialect):
    from sqlalchemy import case, func, select

    pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    lower = func.ailab_search_lower if dialect == "sqlite" else lambda column: func.lower(column.collate("und-x-icu"))
    prompt_hit = lower(window.c.prompt).like(pattern.lower(), escape="\\")
    answer_hit = lower(window.c.summary).like(pattern.lower(), escape="\\")
    text_column = case((prompt_hit, window.c.prompt), else_=window.c.summary)
    position = func.instr(lower(text_column), q.lower()) if dialect == "sqlite" else func.strpos(lower(text_column), q.lower())
    left = case((position > 40, position - 40), else_=1)
    return (
        select(
            window.c.id,
            prompt_hit.label("prompt_hit"),
            func.substr(text_column, left, 120).label("snippet"),
            (position - left).label("hit_start"),
            case((window.c.summary.contains("```openui"), window.c.summary), else_=None).label("ui_summary"),
        )
        .where(prompt_hit | answer_hit)
        .order_by(window.c.created_at.desc(), window.c.id.desc())
        .limit(1)
    )


def _legacy_page(db, user_id, q, limit, state):
    from fastapi import HTTPException
    from sqlalchemy import func, select

    fingerprint = hashlib.sha256((user_id + "\0" + q).encode()).hexdigest()
    if state is None:
        state = {"key": fingerprint, "before": None, "current": None, "run_before": None}
    items, work = [], 0
    conversations = list(db.scalars(_legacy_conversation_scan(user_id, state["before"])))
    for conv in conversations[:LEGACY_CONVERSATION_BATCH]:
        continuing = state["current"] == conv.id
        if state["current"] and not continuing:
            raise HTTPException(422, "搜索游标已过期")
        title_snippet, highlight = _legacy_snippet(conv.title, q)
        match, run_id, value = None, None, None
        if highlight:
            match, value = "title", title_snippet
        else:
            while work < LEGACY_RUN_BUDGET:
                window = _legacy_run_scan(conv.id, state["run_before"] if continuing else None)
                row = db.execute(_legacy_match_query(window, q, db.bind.dialect.name)).first()
                work += LEGACY_RUN_BATCH
                if row:
                    match, run_id = ("prompt" if row.prompt_hit else "answer"), row.id
                    answer = None
                    if row.ui_summary and not row.prompt_hit:
                        from ailab_platform.domain.answer_parts import for_history

                        answer = for_history(row.ui_summary, keep_ui=False, limit=len(row.ui_summary))
                    value, highlight = _legacy_snippet(answer if answer is not None else row.snippet, q)
                    if not highlight and answer is None:
                        start = max(0, row.hit_start)
                        highlight = [[start, min(len(value), start + len(q))]]
                    break
                tail = db.execute(select(window.c.id, window.c.created_at).order_by(window.c.created_at, window.c.id).limit(1)).first()
                count = db.scalar(select(func.count()).select_from(window))
                if count < LEGACY_RUN_BATCH:
                    state.update(current=None, run_before=None)
                    break
                state.update(current=conv.id, run_before=[tail.created_at.isoformat(), tail.id])
                continuing = True
            if not match and work >= LEGACY_RUN_BUDGET and state["current"]:
                return items, state
        if match:
            items.append(
                {
                    "conversation_id": conv.id,
                    "title": conv.title,
                    "project_id": conv.project_id,
                    "run_id": run_id,
                    "match": match,
                    "snippet": value,
                    "highlight": highlight,
                    "updated_at": conv.updated_at,
                }
            )
        state.update(before=[conv.updated_at.isoformat(), conv.id], current=None, run_before=None)
        if len(items) >= limit or work >= LEGACY_RUN_BUDGET:
            return items, state
    if len(conversations) > LEGACY_CONVERSATION_BATCH:
        return items, state
    state.update(before=None, current=None, run_before=None)
    return items, None


def _legacy_search(db, user_id, query, limit):
    items, state = [], None
    for _ in range(LEGACY_MAX_PAGES):
        page, state = _legacy_page(db, user_id, query, limit, state)
        items.extend(page)
        if len(items) >= limit or state is None:
            break
    return items[:limit]


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))]


def _storage_snapshot(db, url: str) -> dict:
    """Keep logical database pages separate from WAL capacity and from the search-only allocation."""
    from sqlalchemy import text

    if db.bind.dialect.name == "sqlite":
        page_size = db.execute(text("PRAGMA page_size")).scalar_one()
        allocated = db.execute(text("PRAGMA page_count")).scalar_one() * page_size
        free = db.execute(text("PRAGMA freelist_count")).scalar_one() * page_size
        path = urlsplit(url).path
        files = {
            name: os.path.getsize(path + suffix) if os.path.exists(path + suffix) else 0
            for name, suffix in (("main_file_bytes", ""), ("wal_file_bytes", "-wal"), ("shm_file_bytes", "-shm"))
        }
        search = db.execute(
            text(
                "SELECT coalesce(sum(pgsize),0) FROM dbstat WHERE name IN "
                "('conversation_search','sqlite_autoindex_conversation_search_1',"
                "'ix_conversation_search_user','search_index_state') OR name LIKE 'conversation_search_fts%'"
            )
        ).scalar_one()
        return {"database_bytes": allocated, "used_bytes": allocated - free, "free_bytes": free, "search_bytes": search, **files}
    allocated = db.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
    search = db.execute(
        text("SELECT pg_total_relation_size('conversation_search') + pg_total_relation_size('search_index_state')")
    ).scalar_one()
    return {"database_bytes": allocated, "search_bytes": search}


def _text_storage(db) -> dict:
    from sqlalchemy import text

    size = "length(CAST({} AS BLOB))" if db.bind.dialect.name == "sqlite" else "octet_length({})"
    row = db.execute(
        text(
            "SELECT coalesce(sum(length(prompt)),0), coalesce(sum(length(summary)),0), "
            f"coalesce(sum({size.format('prompt')}),0), coalesce(sum({size.format('summary')}),0) FROM runs"
        )
    ).one()
    return dict(zip(("prompt_chars", "answer_chars", "prompt_utf8_bytes", "answer_utf8_bytes"), row, strict=True))


def main() -> None:
    args = _parse()
    with tempfile.TemporaryDirectory(prefix="ailab-search-bench-") as scratch:
        _benchmark(args, Path(scratch))


def _benchmark(args: argparse.Namespace, scratch: Path) -> None:
    url = _configure_database(args, scratch)

    from ailab_platform.db import SessionLocal, init_db
    from ailab_platform.search_index import index_pending, run_search

    init_db()
    with SessionLocal() as db:
        user_id, rare = _seed(db, args.conversations, args.runs_per_conversation, args.text_chars)
        text_storage = _text_storage(db)
        before = _storage_snapshot(db, url)
        started = time.perf_counter()
        while index_pending(db, run_limit=500, conversation_limit=500):
            pass
        build_seconds = time.perf_counter() - started
        after = _storage_snapshot(db, url)
        queries = {
            "rare_oldest": rare,
            "common_two_char": "模型",
            "phrase": "部署 评估",
            "latin": "deploy",
            "title": TITLE_NEEDLE,
        }
        rows = []
        for name, query in queries.items():
            run_search(db, user_id, query, 20)  # warm up
            indexed = []
            for _ in range(args.iterations):
                started = time.perf_counter()
                new_items = run_search(db, user_id, query, 20)
                indexed.append((time.perf_counter() - started) * 1000)
            legacy = []
            for _ in range(args.iterations):
                started = time.perf_counter()
                old_items = _legacy_search(db, user_id, query, 20)
                legacy.append((time.perf_counter() - started) * 1000)
            rows.append(
                {
                    "query": name,
                    "text": query,
                    "indexed_ms_p50": round(statistics.median(indexed), 1),
                    "indexed_ms_p95": round(_percentile(indexed, 0.95), 1),
                    "legacy_ms_p50": round(statistics.median(legacy), 1),
                    "legacy_ms_p95": round(_percentile(legacy, 0.95), 1),
                    "indexed_hits": len(new_items),
                    "legacy_hits": len(old_items),
                    "same_first_conversation": bool(
                        new_items and old_items and new_items[0]["conversation_id"] == old_items[0]["conversation_id"]
                    ),
                }
            )
        print(
            json.dumps(
                {
                    "database": url.split("://", 1)[0],
                    "conversations": args.conversations,
                    "runs": args.conversations * args.runs_per_conversation,
                    "index_build_seconds": round(build_seconds, 2),
                    "text_storage": text_storage,
                    "storage_before_index": before,
                    "storage_after_index": after,
                    "database_growth_bytes": after["database_bytes"] - before["database_bytes"],
                    "database_growth_percent": round((after["database_bytes"] / before["database_bytes"] - 1) * 100, 2),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
