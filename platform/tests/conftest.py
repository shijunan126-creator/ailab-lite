"""Test bootstrap: point every adapter at an isolated temp profile."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import traceback
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

_TMP = tempfile.mkdtemp(prefix="ailab-platform-test-")

# pytest-xdist runs one process per worker ("gw0", "gw1", ...). Each worker already has its own _TMP (and so its own
# SQLite file); it also gets its own PostgreSQL databases and Redis database, because tests wipe the public schema
# and flush Redis. Without xdist the URLs are used as given.
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")


def _worker_postgres(url: str | None) -> str | None:
    if not url or not _WORKER:
        return url
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    base = make_url(url)
    name = f"{base.database}_{_WORKER}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            if conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}).scalar() is None:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()
    return base.set(database=name).render_as_string(hide_password=False)


def _worker_redis(url: str) -> str:
    if not _WORKER:
        return url
    parts = urlsplit(url)
    db = int(parts.path.strip("/") or 0) - int(_WORKER.removeprefix("gw"))
    if db < 0:
        raise RuntimeError(f"{url}: not enough Redis databases below it for xdist worker {_WORKER}; use fewer workers")
    return urlunsplit(parts._replace(path=f"/{db}"))


for _name in ("AILAB_TEST_DATABASE_URL", "AILAB_TEST_POSTGRES_URL"):
    if os.environ.get(_name):
        os.environ[_name] = _worker_postgres(os.environ[_name])
if os.environ.get("AILAB_TEST_REDIS_URL"):
    os.environ["AILAB_TEST_REDIS_URL"] = _worker_redis(os.environ["AILAB_TEST_REDIS_URL"])

# Tests only talk to loopback servers. httpx honours the system proxy (trust_env), and a developer's macOS proxy
# that also captures 127.0.0.1 added 0.2-0.5s to every scripted model request (test_prefix_cache: 3m locally).
_LOOPBACK = "127.0.0.1,localhost,::1"
for _name in ("NO_PROXY", "no_proxy"):
    os.environ[_name] = ",".join(filter(None, (os.environ.get(_name), _LOOPBACK)))

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
os.environ["AILAB_REDIS_URL"] = _worker_redis("redis://127.0.0.1:6379/15")
os.environ["AILAB_MAX_ACTIVE_RUNS_PER_USER"] = "1000"
os.environ["AILAB_IMAGE_SEARCH_PROVIDER"] = "mock"  # never call Openverse from tests
# Retries are exercised for their count and accounting, not their pacing; tests of the backoff set it themselves.
os.environ["AILAB_LLM_RETRY_BACKOFF_SECONDS"] = "0.001"
os.environ["AILAB_WATCHDOG_MODE"] = "off"

# Local storage discovery stops after DISCOVERY_ENTRIES entries or DISCOVERY_SECONDS (0.1s), whichever comes first,
# and lists nothing until it is done. Test roots are tiny, so the entry bound alone finishes them in one call; the
# wall-clock bound only made listings depend on how busy the runner was (an xdist CI run starved it).
# Incremental discovery is still exercised through discover(limit=...) in test_local_object_index.
from ailab_platform.adapters import local_object_index as _local_object_index  # noqa: E402

_local_object_index.DISCOVERY_SECONDS = 60.0  # scripted models answer in order; tests that need the judge turn it on


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
    """Dream admission counts live leases and running interactive Runs across the whole database, so whatever a test
    leaves live would throttle the next one. Only live rows are compared: snapshotting every Run id made each test
    slower than the last and pushed the PostgreSQL suite past its CI limit."""
    from sqlalchemy import inspect, or_, select, update

    from ailab_platform.db import SessionLocal
    from ailab_platform.models import MemoryDreamRun, Run

    def live(db):
        jobs = set(
            db.scalars(
                select(MemoryDreamRun.id).where(
                    or_(
                        MemoryDreamRun.status.in_(("pending", "running")),
                        MemoryDreamRun.claimed_until.is_not(None),
                        MemoryDreamRun.report["chain_running"].as_boolean().is_(True),
                    )
                )
            )
        )
        return jobs, set(db.scalars(select(Run.id).where(Run.status == "running")))

    with SessionLocal() as db:
        ready = inspect(db.get_bind()).has_table("memory_dream_runs")
        before_jobs, before_runs = live(db) if ready else (set(), set())
    yield
    if not ready:
        return
    with SessionLocal() as db:
        jobs, runs = live(db)
        left = jobs - before_jobs
        if left:
            db.execute(update(MemoryDreamRun).where(MemoryDreamRun.id.in_(left)).values(claimed_until=None, claim_token=None))
            db.execute(
                update(MemoryDreamRun)
                .where(MemoryDreamRun.id.in_(left), MemoryDreamRun.status.in_(("pending", "running")))
                .values(status="failed")
            )
            for row in db.scalars(select(MemoryDreamRun).where(MemoryDreamRun.id.in_(left))):
                if (row.report or {}).get("chain_running"):
                    row.report = {**row.report, "chain_running": False}
        if runs - before_runs:
            db.execute(update(Run).where(Run.id.in_(runs - before_runs)).values(status="canceled"))
        db.commit()


@pytest.fixture(autouse=True)
def isolate_model_pressure(monkeypatch):
    from ailab_platform import model_pressure

    monkeypatch.setattr(model_pressure, "_until", 0.0)
