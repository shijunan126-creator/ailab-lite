"""The shares migration (0027, after 0026) round-trips on SQLite and throwaway PostgreSQL."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from ailab_platform.models import Base
from tests.test_migrations import _downgrade_to, _seed_user_and_run, _upgrade_to, _version
from tests.test_migrations import engine as engine


def test_shares_up_down_up(engine):
    _upgrade_to(engine, "0024")
    with engine.begin() as conn:
        _seed_user_and_run(conn)
    _upgrade_to(engine, "head")
    assert _version(engine) == "0028"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO shares(id,owner_id,kind,file_id,audience,title,created_at,updated_at) VALUES "
                "('s','u1','file',NULL,'people','test','2026-01-01','2026-01-01')"
            )
        )
        conn.execute(text("INSERT INTO share_recipients(share_id,user_id) VALUES ('s','u1')"))
    _downgrade_to(engine, "0026")
    assert "shares" not in inspect(engine).get_table_names()
    _upgrade_to(engine, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT prompt FROM runs WHERE id='r1'")).scalar() == "p"
        assert compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata) == []
