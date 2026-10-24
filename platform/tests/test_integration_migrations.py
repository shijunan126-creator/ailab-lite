"""The merged 0022/0023/0024 chain is linear and round-trips existing history."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from ailab_platform.migrate import alembic_config
from ailab_platform.models import Base
from tests.test_migrations import _downgrade_to, _seed_user_and_run, _upgrade_to, _version
from tests.test_migrations import engine as engine


def test_integrated_feature_chain_up_down_reup(engine):
    scripts = ScriptDirectory.from_config(alembic_config())
    assert scripts.get_heads() == ["0026"]
    assert [scripts.get_revision(rev).down_revision for rev in ("0022", "0023", "0024", "0025", "0026")] == ["0021", "0022", "0023", "0024", "0025"]
    if engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
    _upgrade_to(engine, "0021")
    with engine.begin() as conn:
        _seed_user_and_run(conn)
        conn.execute(
            text(
                "INSERT INTO conversations(id,user_id,title,model,created_at,updated_at) "
                "VALUES ('c1','u1','保留会话','m','2026-01-01','2026-01-01')"
            )
        )
        conn.execute(text("UPDATE runs SET conversation_id='c1',summary='保留回答' WHERE id='r1'"))
    for revision in ("0022", "0023", "0024", "0025"):
        _upgrade_to(engine, revision)
        assert _version(engine) == revision
    for revision in ("0024", "0023", "0022", "0021"):
        _downgrade_to(engine, revision)
        assert _version(engine) == revision
    assert "scheduled_tasks" not in inspect(engine).get_table_names()
    for revision in ("0022", "0023", "0024", "0025"):
        _upgrade_to(engine, revision)
    _upgrade_to(engine, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT conversation_id,summary,parent_run_id FROM runs WHERE id='r1'")).one() == ("c1", "保留回答", None)
        assert conn.execute(text("SELECT active_run_id FROM conversations WHERE id='c1'")).scalar() == "r1"
        assert compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata) == []
        if engine.dialect.name == "sqlite":
            assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
