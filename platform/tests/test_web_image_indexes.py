"""Selective address/expiry/ref queries must stay indexed as historical images grow."""

import json
from datetime import timedelta

from sqlalchemy import text

from ailab_platform.migrate import run_migrations
from ailab_platform.models import WebImage, WebImageRef, utcnow
from tests.test_migrations import _seed_user_and_run, engine  # noqa: F401


def test_registry_lookup_and_collection_use_indexes(engine):  # noqa: F811
    run_migrations(engine)
    now = utcnow()
    with engine.begin() as conn:
        _seed_user_and_run(conn)
        conn.execute(
            WebImage.__table__.insert(),
            [
                dict(
                    id=f"img_{i:012d}",
                    url_hash=f"{i:064x}",
                    url=f"https://example.com/{i}.jpg",
                    thumbnail_url="",
                    page_url="",
                    title="",
                    expires_at=now + timedelta(days=1) if i else now - timedelta(days=1),
                )
                for i in range(10000)
            ],
        )
        conn.execute(
            WebImageRef.__table__.insert(),
            [dict(run_id="r1", image_id=f"img_{i:012d}", expires_at=None if i else now - timedelta(days=1)) for i in range(10000)],
        )
        conn.execute(text("ANALYZE web_images"))
        conn.execute(text("ANALYZE web_image_refs"))
        queries = [
            ("SELECT id FROM web_images WHERE url_hash = :hash", {"hash": f"{88:064x}"}, "uq_web_images_url_hash"),
            ("SELECT id FROM web_images WHERE expires_at < :now ORDER BY expires_at, id LIMIT 20", {"now": now}, "ix_web_images_expiry"),
            ("SELECT run_id FROM web_image_refs WHERE image_id = :id", {"id": "img_000000000088"}, "ix_web_image_refs_image"),
            (
                "SELECT run_id, image_id FROM web_image_refs WHERE expires_at < :now ORDER BY expires_at, run_id, image_id LIMIT 20",
                {"now": now},
                "ix_web_image_refs_expiry",
            ),
        ]
        for sql, params, index in queries:
            prefix = "EXPLAIN (ANALYZE, FORMAT JSON) " if engine.dialect.name == "postgresql" else "EXPLAIN QUERY PLAN "
            plan = json.dumps([list(row) for row in conn.execute(text(prefix + sql), params)], default=str)
            assert index in plan, plan
