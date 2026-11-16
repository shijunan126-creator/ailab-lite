"""Services other than the API never load the database layer (AGENTS.md §4): it costs ~30 MiB resident in pods
whose limit is 256 MiB, and an import is how a stray query starts."""

import subprocess
import sys

import pytest

DATABASE_MODULES = ("ailab_platform.models", "ailab_platform.db", "sqlalchemy", "alembic")


@pytest.mark.parametrize("service", ["agent", "broker", "worker"])
def test_service_does_not_import_the_database_layer(service):
    probe = f"import sys, ailab_platform.{service}.main\nprint(','.join(m for m in {DATABASE_MODULES!r} if m in sys.modules))"
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=True)
    assert result.stdout.strip() == ""
