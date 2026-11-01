"""Test bootstrap: point every adapter at an isolated temp profile."""

from __future__ import annotations

import asyncio
import os
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
