"""Single-instance DB contract; PostgreSQL uses a private disposable schema."""

import os
import uuid

import pytest
from sqlalchemy import create_engine, event, text


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request, tmp_path):
    if request.param == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'contract.db'}")

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        yield engine
        engine.dispose()
        return
    if not (url := os.environ.get("AILAB_TEST_POSTGRES_URL")):
        pytest.skip("AILAB_TEST_POSTGRES_URL not set")
    admin = create_engine(url)
    schema = "contract_" + uuid.uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
