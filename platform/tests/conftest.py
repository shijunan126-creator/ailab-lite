"""Test bootstrap: point every adapter at an isolated temp profile."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import traceback

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

_TMP = tempfile.mkdtemp(prefix="ailab-platform-test-")

# Never read the developer's platform/.env: it may hold a real model key and a different model name.
os.environ["AILAB_ENV_FILE"] = os.path.join(_TMP, "no-such.env")
os.environ.setdefault("AILAB_ENV", "test")
os.environ["AILAB_DATA_DIR"] = _TMP
os.environ["AILAB_STORAGE_ROOT"] = os.path.join(_TMP, "storage")
# AILAB_TEST_DATABASE_URL runs the whole suite on another database (a throwaway PostgreSQL); it is wiped first.
_DB = os.environ.get("AILAB_TEST_DATABASE_URL")
if _DB:
    from sqlalchemy import create_engine, text

    _eng = create_engine(_DB)
    with _eng.begin() as _conn:
        _conn.execute(text("DROP SCHEMA public CASCADE"))
        _conn.execute(text("CREATE SCHEMA public"))
    _eng.dispose()
os.environ["AILAB_DATABASE_URL"] = _DB or f"sqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ["AILAB_QUEUE_BACKEND"] = "memory"
os.environ["AILAB_SANDBOX_PROVIDER"] = "mock"
os.environ["AILAB_MODELS_FILE"] = os.path.join(os.path.dirname(__file__), "models.test.toml")
os.environ["AILAB_DEV_TOKEN"] = "test-token"
os.environ["AILAB_SERVICE_TOKEN"] = "test-service"
os.environ["AILAB_REDIS_URL"] = "redis://127.0.0.1:6379/15"
os.environ["AILAB_MAX_ACTIVE_RUNS_PER_USER"] = "1000"
os.environ["AILAB_IMAGE_SEARCH_PROVIDER"] = "mock"  # never call Openverse from tests
os.environ["AILAB_WATCHDOG_MODE"] = "off"  # scripted models answer in order; tests that need the judge turn it on


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    if exitstatus:
        if reporter:
            reporter.write_line("Isolated test state retained for failure inspection: " + _TMP)
        return
    from ailab_platform.db import engine
    from ailab_platform.storage import close_storage

    close_storage()
    engine.dispose()
    shutil.rmtree(_TMP)
    if reporter:
        reporter.write_line("Removed isolated test state: " + _TMP)


@pytest.fixture(autouse=True)
def no_sql_on_serving_loop():
    """Catch blocking SQL even when an endpoint/background job swallows its error."""
    from ailab_platform.api import main

    violations = set()

    def before_cursor_execute(*_args):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if loop is not main.SERVING_LOOP:
            return
        frames = traceback.extract_stack()
        sites = [
            f"{frame.filename.split('ailab_platform/', 1)[1]}:{frame.lineno} in {frame.name}"
            for frame in frames
            if "ailab_platform/" in frame.filename
        ]
        violations.add(" -> ".join(sites))

    event.listen(Engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield
    finally:
        event.remove(Engine, "before_cursor_execute", before_cursor_execute)
    if violations:
        pytest.fail("Blocking SQL on API serving loop:\n" + "\n".join(sorted(violations)))


@pytest.fixture(autouse=True)
def release_test_document_jobs():
    from sqlalchemy import delete, inspect, select

    from ailab_platform.db import SessionLocal
    from ailab_platform.models import DocumentOperation, DocumentPreparation

    with SessionLocal() as db:
        ready = inspect(db.get_bind()).has_table("document_operations")
        before = set(db.scalars(select(DocumentOperation.id))) if ready else set()
        preparations = set(db.scalars(select(DocumentPreparation.id))) if ready else set()
    yield
    with SessionLocal() as db:
        if not inspect(db.get_bind()).has_table("document_operations"):
            return
        from ailab_platform.models import OutboxMessage

        created = select(DocumentOperation.id).where(DocumentOperation.id.not_in(before))
        db.execute(delete(OutboxMessage).where(OutboxMessage.id.in_(created)))
        db.execute(delete(DocumentOperation).where(DocumentOperation.id.not_in(before)))
        db.execute(delete(DocumentPreparation).where(DocumentPreparation.id.not_in(preparations)))
        db.commit()


@pytest.fixture(autouse=True)
def release_test_memory_jobs():
    from sqlalchemy import inspect, select, update

    from ailab_platform.db import SessionLocal
    from ailab_platform.models import MemoryDreamRun, Run

    with SessionLocal() as db:
        ready = inspect(db.get_bind()).has_table("memory_dream_runs")
        before = set(db.scalars(select(MemoryDreamRun.id))) if ready else set()
        before_runs = set(db.scalars(select(Run.id))) if ready else set()
    yield
    with SessionLocal() as db:
        if inspect(db.get_bind()).has_table("memory_dream_runs"):
            db.execute(update(MemoryDreamRun).where(MemoryDreamRun.id.not_in(before)).values(claimed_until=None, claim_token=None))
            db.execute(
                update(MemoryDreamRun)
                .where(MemoryDreamRun.id.not_in(before), MemoryDreamRun.status.in_(("pending", "running")))
                .values(status="failed")
            )
            for row in db.scalars(select(MemoryDreamRun).where(MemoryDreamRun.id.not_in(before))):
                if (row.report or {}).get("chain_running"):
                    row.report = {**row.report, "chain_running": False}
            db.execute(update(Run).where(Run.id.not_in(before_runs), Run.status == "running").values(status="canceled"))
            db.commit()


@pytest.fixture(autouse=True)
def isolate_model_pressure(monkeypatch):
    from ailab_platform import model_pressure

    monkeypatch.setattr(model_pressure, "_until", 0.0)
