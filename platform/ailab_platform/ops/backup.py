"""Offline-verifiable DB/object snapshots. No secrets are written to a backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import tomllib
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from ..config import settings
from ..models import FileObject, Run
from ..storage import LocalStorage, _safe_key, get_storage


@contextmanager
def _empty_target(target: Path):
    target = target.resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("target must be empty")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    yield target


def _sha(path: Path) -> str:
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _redacted_config(config) -> dict:
    # Allow-list operational values; new secret fields cannot accidentally leak.
    names = (
        "env",
        "storage_backend",
        "library_quota_bytes",
        "max_upload_bytes",
        "max_parse_bytes",
        "workspace_ttl_days",
        "presign_staging_ttl_seconds",
        "orphan_objects_grace_seconds",
        "run_events_retention_days",
        "conversation_purge_days",
        "file_trash_days",
        "gc_batch_size",
    )
    return {name: getattr(config, name) for name in names}


def _models(source: Path, target: Path):
    # Catalogs normally name env vars. Redact literal credentials and URL userinfo/query strings too.
    content = source.read_text()
    content = re.sub(
        r"(?im)^(\s*[\w.-]*(?:key|token|password|secret)\s*=).*$",
        lambda m: m[1] + ' "REDACTED"',
        content,
    )
    content = re.sub(
        r'(https?://)[^\s"\']+',
        lambda m: m[0].split("?")[0].split("#")[0] if "@" not in m[0] else m[1] + m[0].split("@", 1)[1].split("?")[0],
        content,
    )

    def check(table):
        if isinstance(table, dict):
            for name, value in table.items():
                if re.search(r"(?:key|token|password|secret)$", name, re.I) and value != "REDACTED":
                    raise ValueError("catalog contains a credential that cannot be safely redacted")
                check(value)
        elif isinstance(table, list):
            for item in table:
                check(item)

    check(tomllib.loads(content))
    target.write_text(content)


@contextmanager
def _database_snapshot(config, target):
    url = make_url(config.database_url)
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            raise ValueError("backup requires a persistent SQLite database")
        database = target / "platform.db"
        with closing(sqlite3.connect(f"file:{Path(url.database).resolve()}?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(database)) as dst:
                deadline = time.monotonic() + config.backup_timeout_seconds

                def progress(_status, _remaining, _total):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("SQLite snapshot exceeded backup deadline")

                src.backup(dst, pages=256, progress=progress, sleep=0.05)
        engine = create_engine(f"sqlite:///{database}")
        try:
            with Session(engine) as db:
                yield db, "sqlite", database.name
        finally:
            engine.dispose()
    elif url.get_backend_name() == "postgresql":
        engine = create_engine(url, connect_args={"connect_timeout": 5})
        try:
            with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
                with conn.begin():
                    snapshot = conn.scalar(text("SELECT pg_export_snapshot()"))
                    env = os.environ.copy()
                    env.update(
                        PGHOST=url.host or "localhost",
                        PGPORT=str(url.port or 5432),
                        PGUSER=url.username or "",
                        PGPASSWORD=url.password or "",
                        PGDATABASE=url.database or "",
                    )
                    subprocess.run(
                        ["pg_dump", "--no-owner", "--no-acl", f"--snapshot={snapshot}", "--file", str(target / "database.sql")],
                        env=env,
                        check=True,
                        timeout=config.backup_timeout_seconds,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                    )
                    with Session(bind=conn) as db:
                        yield db, "postgresql", "database.sql"
        finally:
            engine.dispose()
    else:
        raise ValueError("unsupported database backend")


def _references(db):
    for file in db.scalars(select(FileObject).execution_options(yield_per=100)):
        if (file.meta or {}).get("processing") == "awaiting_upload":
            continue
        yield {"key": file.storage_key, "size": file.size, "sha256": file.sha256, "owner": file.id}
        for name in ("derived_key", "text_key", "thumb_key"):
            if key := (file.meta or {}).get(name):
                yield {"key": key, "owner": file.id}
    for run in db.scalars(select(Run).execution_options(yield_per=100)):
        if (run.result or {}).get("workspace_saved"):
            yield {"key": f"workspaces/{run.id}.tgz", "owner": run.id, "optional": True}


def create(target: Path, *, config=settings, storage=None) -> dict:
    storage = storage or get_storage()
    deadline = time.monotonic() + config.backup_timeout_seconds
    with _empty_target(target) as target:
        with _database_snapshot(config, target) as (db, backend, database):
            objects = target / "storage"
            sink = LocalStorage(objects)
            count = total = 0
            # A disk-backed index avoids an unbounded set of keys in memory.
            with closing(sqlite3.connect(target / ".seen.db")) as seen:
                seen.execute("CREATE TABLE seen (key TEXT PRIMARY KEY)")
                with (target / "objects.jsonl").open("w") as manifest, (target / "references.jsonl").open("w") as refs:

                    def save(ref):
                        nonlocal count, total
                        key = _safe_key(ref["key"])
                        if ref.get("optional") and not storage.exists(key):
                            return
                        refs.write(json.dumps(ref) + "\n")
                        if seen.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone():
                            return
                        info = storage.info(key)
                        if "size" in ref and ref["size"] != info.size:
                            raise ValueError(f"database size mismatch for {key}")
                        with storage.open_stream(key) as chunks:

                            def bounded():
                                for chunk in chunks:
                                    if time.monotonic() >= deadline:
                                        raise TimeoutError("object snapshot exceeded backup deadline")
                                    yield chunk

                            sink.put_stream(key, bounded(), info.content_type, max_bytes=info.size)
                        digest = _sha(objects / key)
                        if ref.get("sha256") and ref["sha256"] != digest:
                            raise ValueError(f"database hash mismatch for {key}")
                        entry = {"key": key, "size": info.size, "sha256": digest, "content_type": info.content_type}
                        manifest.write(json.dumps(entry) + "\n")
                        seen.execute("INSERT INTO seen VALUES (?)", (key,))
                        count += 1
                        total += info.size

                    for ref in _references(db):
                        save(ref)
                    # Include owner-bound previews and derived assets not explicitly named in metadata.
                    for prefix, model in (("derived/", FileObject), ("previews/", Run)):
                        cursor = None
                        while True:
                            page = storage.list_prefix(prefix, cursor=cursor, limit=100)
                            for item in page:
                                owner = item.key.split("/")[1]
                                if db.get(model, owner) is not None:
                                    save({"key": item.key, "owner": owner})
                            cursor = page.cursor
                            if cursor is None:
                                break
            (target / ".seen.db").unlink()
        _write_json(target / "config.redacted.json", _redacted_config(config))
        _models(config.models_path, target / "models.toml")
        files = (database, "objects.jsonl", "references.jsonl", "config.redacted.json", "models.toml")
        result = {
            "version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "backend": backend,
            "database": database,
            "objects": count,
            "bytes": total,
            "files": {name: _sha(target / name) for name in files},
        }
        if config.storage_backend == "s3":
            result["s3_sync"] = {"bucket": config.s3_bucket, "manifest": "objects.jsonl"}
        verify(target, _metadata=result)
        _write_json(target / "backup.json", result)  # completion marker only after full verification
        return result


def verify(target: Path, *, _metadata: dict | None = None) -> dict:
    metadata = _metadata or json.loads((target / "backup.json").read_text())
    if metadata["version"] != 1 or metadata["database"] not in ("platform.db", "database.sql"):
        raise ValueError("unsupported backup format")
    for name, digest in metadata["files"].items():
        if _sha(target / _safe_key(name)) != digest:
            raise ValueError(f"backup metadata corrupted: {name}")
    count = total = 0
    # Temporary disk index also verifies references for very large libraries.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ailab-verify-") as work, closing(sqlite3.connect(Path(work) / "index.db")) as index:
        index.execute("CREATE TABLE objects (key TEXT PRIMARY KEY, size INTEGER, sha256 TEXT)")
        with (target / "objects.jsonl").open() as fh:
            for line in fh:
                item = json.loads(line)
                key = _safe_key(item["key"])
                path = target / "storage" / key
                if path.is_symlink() or not path.is_file() or path.stat().st_size != item["size"] or _sha(path) != item["sha256"]:
                    raise ValueError(f"object corrupted or missing: {key}")
                index.execute("INSERT INTO objects VALUES (?, ?, ?)", (key, item["size"], item["sha256"]))
                count += 1
                total += item["size"]
        with (target / "references.jsonl").open() as fh:
            for line in fh:
                ref = json.loads(line)
                row = index.execute("SELECT size, sha256 FROM objects WHERE key=?", (ref["key"],)).fetchone()
                if row is None or ("size" in ref and ref["size"] != row[0]) or (ref.get("sha256") and ref["sha256"] != row[1]):
                    raise ValueError(f"broken object reference: {ref['key']}")
        if metadata["backend"] == "sqlite":
            with closing(sqlite3.connect(f"file:{(target / 'platform.db').resolve()}?mode=ro", uri=True)) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("SQLite integrity check failed")
                for key, size, digest, meta in db.execute("SELECT storage_key, size, sha256, meta FROM files"):
                    if json.loads(meta or "{}").get("processing") == "awaiting_upload":
                        continue
                    row = index.execute("SELECT size, sha256 FROM objects WHERE key=?", (key,)).fetchone()
                    if row is None or row[0] != size or (digest and row[1] != digest):
                        raise ValueError(f"database reference missing: {key}")
    if count != metadata["objects"] or total != metadata["bytes"]:
        raise ValueError("manifest totals differ")
    return {"objects": count, "bytes": total, "backend": metadata["backend"]}


def restore(source: Path, target: Path) -> dict:
    # Refuse a non-empty destination even if verification would fail first.
    with _empty_target(target) as target:
        result = verify(source)
        metadata = json.loads((source / "backup.json").read_text())
        for name in (*metadata["files"], "backup.json"):
            shutil.copyfile(source / name, target / name)
        shutil.copytree(source / "storage", target / "storage")
        verify(target)
        # A PG restore deliberately stages SQL; the operator selects a new empty database explicitly.
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create").add_argument("--target", type=Path, required=True)
    commands.add_parser("verify").add_argument("directory", type=Path)
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("directory", type=Path)
    restore_parser.add_argument("--into", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "create":
        result = create(args.target)
    elif args.command == "verify":
        result = verify(args.directory)
    else:
        result = restore(args.directory, args.into)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
