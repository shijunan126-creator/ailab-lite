"""Small local expiry index for generated directories, independent of the business DB.

Intent commits before mkdir. Normal exit deletes immediately; a crash leaves the
fixed lease plus orphan grace for a bounded startup/periodic sweep. Only paths
created under this service's private root can ever be removed.
"""

import logging
import shutil
import sqlite3
import time
from contextlib import contextmanager
from uuid import uuid4

from .config import settings
from .runtime_context import temporary_deadline_var

log = logging.getLogger("ailab.temporary")


def root(service):
    if service not in {"worker", "broker"}:
        raise ValueError("invalid_temporary_owner")
    return settings.data_dir / (service + "-temporary")


@contextmanager
def index(service):
    directory = root(service)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(directory / "leases.sqlite3", timeout=5)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS leases (token TEXT PRIMARY KEY, deadline REAL NOT NULL)")
        connection.execute("CREATE INDEX IF NOT EXISTS leases_deadline ON leases(deadline, token)")
        yield connection
        connection.commit()
    finally:
        connection.close()


@contextmanager
def directory(service="worker", *, prefix="job", deadline=None):
    token = uuid4().hex
    lease = deadline or temporary_deadline_var.get() or time.time() + 120
    with index(service) as connection:
        connection.execute("INSERT INTO leases(token, deadline) VALUES (?, ?)", (token, lease + settings.derived_orphan_grace_seconds))
    path = root(service) / token
    try:
        path.mkdir(mode=0o700)
        yield str(path)
    finally:
        try:
            shutil.rmtree(path, ignore_errors=False) if path.exists() else None
        except OSError:
            with index(service) as connection:
                connection.execute("UPDATE leases SET deadline=? WHERE token=?", (time.time(), token))
            log.warning("temporary directory cleanup pending purpose=%s", prefix)
        else:
            with index(service) as connection:
                connection.execute("DELETE FROM leases WHERE token=?", (token,))


def sweep(service="worker", *, now=None, limit=None):
    now = now or time.time()
    limit = limit or settings.gc_batch_size
    if not root(service).exists():
        return 0
    removed = 0
    with index(service) as connection:
        candidates = connection.execute(
            "SELECT token FROM leases WHERE deadline<=? ORDER BY deadline, token LIMIT ?", (now, limit)
        ).fetchall()
    for (token,) in candidates:
        if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
            raise ValueError("invalid_temporary_token")
        try:
            path = root(service) / token
            if path.exists():
                shutil.rmtree(path)
            with index(service) as connection:
                connection.execute("DELETE FROM leases WHERE token=? AND deadline<=?", (token, now))
            removed += 1
        except OSError:
            log.warning("temporary crash compensation remains pending service=%s", service)
    return removed
