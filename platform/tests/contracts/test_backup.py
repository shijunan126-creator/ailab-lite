"""SQLite + local storage disaster-recovery drill, including a fresh app process."""

import hashlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ailab_platform.migrate import run_migrations
from ailab_platform.models import Conversation, FileObject, Run, User
from ailab_platform.ops.backup import create, restore, verify
from ailab_platform.storage import LocalStorage


@pytest.fixture
def snapshot(tmp_path):
    from ailab_platform.config import settings

    source = tmp_path / "source"
    store = LocalStorage(source / "storage")
    engine = create_engine(f"sqlite:///{source / 'platform.db'}")
    run_migrations(engine)
    data = b"recoverable file"
    store.put_bytes("uploads/u/f/obj/input.txt", data, "text/plain")
    with Session(engine) as db:
        db.add(User(id="u", email="backup@example.test"))
        db.flush()
        db.add(Conversation(id="c", user_id="u", title="recoverable conversation"))
        db.flush()
        db.add(Run(id="r", user_id="u", conversation_id="c", prompt="hello", summary="restored answer", status="succeeded"))
        db.add(
            FileObject(
                id="f",
                user_id="u",
                filename="input.txt",
                storage_key="uploads/u/f/obj/input.txt",
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                status="ready",
            )
        )
        db.commit()
    config = SimpleNamespace(
        **{**settings.model_dump(), "database_url": str(engine.url), "models_path": settings.models_path, "storage_backend": "local"}
    )
    backup = tmp_path / "backup"
    create(backup, config=config, storage=store)
    engine.dispose()
    return backup, data


def test_backup_restore_verify_and_fresh_app_smoke(snapshot, tmp_path):
    backup, data = snapshot
    target = tmp_path / "restored"
    assert restore(backup, target)["objects"] == 1
    assert verify(target)["objects"] == 1
    env = {key: value for key, value in os.environ.items() if not key.startswith("AILAB_")}
    env.update(
        AILAB_ENV="test",
        AILAB_ENV_FILE=str(tmp_path / "absent.env"),
        AILAB_DATA_DIR=str(target),
        AILAB_DATABASE_URL=f"sqlite:///{target / 'platform.db'}",
        AILAB_STORAGE_ROOT=str(target / "storage"),
        AILAB_QUEUE_BACKEND="memory",
        AILAB_DEV_TOKEN="restore-token",
        AILAB_MODELS_FILE=str(target / "models.toml"),
    )
    script = """
from fastapi.testclient import TestClient
from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal, hash_token
from ailab_platform.models import ApiToken
with TestClient(app) as client:
    with SessionLocal() as db:
        db.add(ApiToken(user_id="u", name="restored-user", token_hash=hash_token("file-token")))
        db.commit()
    headers = {"Authorization": "Bearer file-token"}
    file = client.get("/v1/files/f/content", headers=headers)
    assert file.status_code == 200 and file.content == b"recoverable file", (file.status_code, file.text)
    conv = client.get("/v1/conversations/c", headers=headers)
    assert conv.status_code == 200 and "restored answer" in conv.text, conv.text
"""
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode()
    assert (target / "storage/uploads/u/f/obj/input.txt").read_bytes() == data
    with pytest.raises(ValueError, match="empty"):
        restore(backup, target)


def test_verify_rejects_corrupted_object(snapshot):
    backup, _ = snapshot
    (backup / "storage/uploads/u/f/obj/input.txt").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="corrupted"):
        verify(backup)


def test_manifest_cannot_omit_database_reference(snapshot):
    backup, _ = snapshot
    metadata = json.loads((backup / "backup.json").read_text())
    (backup / "objects.jsonl").write_text("")
    (backup / "references.jsonl").write_text("")
    for name in ("objects.jsonl", "references.jsonl"):
        metadata["files"][name] = hashlib.sha256(b"").hexdigest()
    (backup / "backup.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="database reference"):
        verify(backup)


def test_models_redacts_literal_credentials_and_url_userinfo(tmp_path):
    from ailab_platform.ops.backup import _models

    source, target = tmp_path / "models.toml", tmp_path / "redacted.toml"
    source.write_text('api_key = "private-value" # do not retain\nbase_url = "https://user:password@model.test/v1?token=secret"\n')
    _models(source, target)
    content = target.read_text()
    assert "private-value" not in content and "password" not in content and "token=secret" not in content


def test_postgresql_snapshot_dump_restores_to_new_database(tmp_path):
    import uuid

    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from ailab_platform.config import settings

    if not (url := os.environ.get("AILAB_TEST_POSTGRES_URL")):
        pytest.skip("AILAB_TEST_POSTGRES_URL not set")
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    source_name, restored_name = ("backup_" + uuid.uuid4().hex for _ in range(2))
    source_engine = None
    restored_engine = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{source_name}"'))
            conn.execute(text(f'CREATE DATABASE "{restored_name}"'))
        source_url = make_url(url).set(database=source_name)
        source_engine = create_engine(source_url)
        run_migrations(source_engine)
        with Session(source_engine) as db:
            db.add(User(id="u", email="pg-backup@example.test"))
            db.commit()
        config = SimpleNamespace(
            **{
                **settings.model_dump(),
                "database_url": source_url.render_as_string(hide_password=False),
                "models_path": settings.models_path,
                "storage_backend": "local",
            }
        )
        backup, restored = tmp_path / "pg-backup", tmp_path / "pg-restored"
        create(backup, config=config, storage=LocalStorage(tmp_path / "empty-objects"))
        assert restore(backup, restored)["backend"] == "postgresql"
        env = os.environ.copy()
        env.update(
            PGHOST=source_url.host,
            PGPORT=str(source_url.port or 5432),
            PGUSER=source_url.username,
            PGPASSWORD=source_url.password or "",
            PGDATABASE=restored_name,
        )
        result = subprocess.run(
            ["psql", "-v", "ON_ERROR_STOP=1", "-f", str(restored / "database.sql")], env=env, capture_output=True, timeout=60
        )
        assert result.returncode == 0, result.stderr.decode()
        restored_engine = create_engine(source_url.set(database=restored_name))
        with Session(restored_engine) as db:
            assert db.get(User, "u").email == "pg-backup@example.test"
    finally:
        if source_engine:
            source_engine.dispose()
        if restored_engine:
            restored_engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{source_name}" WITH (FORCE)'))
            conn.execute(text(f'DROP DATABASE IF EXISTS "{restored_name}" WITH (FORCE)'))
        admin.dispose()
