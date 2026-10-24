"""0028 up/down/up with brand promotion and real SQLite/PG query plans."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from ailab_platform.config import settings
from ailab_platform.models import Base
from tests.test_migrations import _downgrade_to, _seed_user_and_run, _upgrade_to
from tests.test_migrations import engine as engine


def test_template_migration_promotes_brand_and_roundtrips(engine, monkeypatch):
    monkeypatch.setattr(settings, "file_queue", "ailab:stream:template-migration-test")
    _upgrade_to(engine, "0027")
    with engine.begin() as conn:
        _seed_user_and_run(conn)
        conn.execute(
            text(
                "INSERT INTO files "
                "(id,user_id,filename,storage_key,purpose,status,meta,created_at,content_type,size,sha256,version) "
                "VALUES "
                "('f1','u1','Legacy.pptx','brand/f1','brand','ready','{}','2026-01-01','application/octet-stream',10,'abc',1)"
            )
        )
        conn.execute(text("INSERT INTO platform_settings(key,value) VALUES ('brand',:value)"), {"value": '{"pptx_template_file_id":"f1"}'})
    _upgrade_to(engine, "0028")
    with engine.connect() as conn:
        row = conn.execute(text("SELECT kind,scope,owner_id,name,file_id,created_by FROM templates")).one()
        assert row == ("pptx", "company", None, "Legacy", "f1", "u1")
        assert conn.execute(text("SELECT purpose,status FROM files WHERE id='f1'")).one() == ("template", "pending")
        assert conn.execute(text("SELECT topic FROM outbox WHERE id LIKE 'tpl_migrate_%'")).scalar() == settings.file_queue
    _upgrade_to(engine, "head")
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata) == []
    _downgrade_to(engine, "0027")
    assert "templates" not in inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT purpose,status FROM files WHERE id='f1'")).one() == ("brand", "ready")
        assert conn.execute(text("SELECT count(*) FROM outbox WHERE id LIKE 'tpl_migrate_%'")).scalar() == 0
    _upgrade_to(engine, "0028")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM templates")).scalar() == 1
