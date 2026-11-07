"""Capacity, publication fences and cleanup against real SQL/storage, including concurrent writers."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker

from ailab_platform.api import derived_assets as assets
from ailab_platform.config import settings
from ailab_platform.models import Base, DerivedAsset, DerivedBudget, FileObject, Run, User
from ailab_platform.storage import LocalStorage


@pytest.fixture(params=["sqlite", "postgres"])
def ledger(request, tmp_path):
    schema = "derived_test_" + uuid4().hex
    admin = None
    if request.param == "postgres":
        url = os.environ.get("AILAB_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("AILAB_TEST_POSTGRES_URL not set")
        admin = create_engine(url)
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def pragmas(connection, _):
            connection.create_function("ailab_search_lower", 1, lambda value: value.lower() if value else "", deterministic=True)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")

    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    Base.metadata.create_all(engine)
    with factory.begin() as db:
        db.add(User(id="u", email="derived@test.local", display_name="Derived"))
        db.add(User(id="u2", email="derived2@test.local", display_name="Second"))
        db.flush()
        db.add(
            FileObject(
                id="f", user_id="u", filename="report.pdf", storage_key="uploads/u/f", sha256="source", meta={"scan": {"verdict": "clean"}}
            )
        )
        db.add(
            FileObject(
                id="g",
                user_id="u2",
                filename="report.pdf",
                storage_key="uploads/u2/g",
                sha256="source",
                meta={"scan": {"verdict": "clean"}},
            )
        )
        db.add(Run(id="r", user_id="u", prompt="read", model="test", title="test"))
        db.add(DerivedBudget(id=1))
    try:
        yield factory, LocalStorage(tmp_path / "storage")
    finally:
        engine.dispose()
        if admin:
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def initialize(factory, storage):
    for _ in range(3):
        with factory() as db:
            assets.backfill_batch(db, storage)
            db.commit()


def write(factory, storage, key, data=b"data", **options):
    with factory() as db:
        grant = assets.reserve(db, key, len(data), "source", **options)
        db.commit()
    storage.put_bytes(key, data)
    with factory() as db:
        result = assets.finish(db, key, grant["token"], len(data), hashlib.sha256(data).hexdigest(), now=options.get("now"))
        db.commit()
    return grant, result


def totals(factory):
    with factory() as db:
        row = db.get(DerivedBudget, 1)
        return row.used_bytes, row.reserved_bytes


def test_initialization_and_source_fences_are_fail_closed(ledger):
    factory, storage = ledger
    with factory() as db:
        with pytest.raises(HTTPException, match="derived_accounting_initializing"):
            assets.reserve(db, "derived/f/a.png", 4, "source")
        db.rollback()
    initialize(factory, storage)
    with factory() as db:
        with pytest.raises(HTTPException, match="document_version_changed"):
            assets.reserve(db, "derived/f/a.png", 4, "old")
        db.rollback()
        file = db.get(FileObject, "f")
        file.meta = {"scan": {"verdict": "unavailable"}}
        db.commit()
        with pytest.raises(HTTPException, match="file_source_unavailable"):
            assets.reserve(db, "derived/f/a.png", 4, "source")
    assert totals(factory) == (0, 0)


def test_reserve_settle_replacement_and_idempotent_completion(ledger):
    factory, storage = ledger
    initialize(factory, storage)
    grant, _ = write(factory, storage, "derived/f/page.png")
    assert totals(factory) == (4, 0)
    with factory() as db:
        assets.finish(db, grant["key"], grant["token"], 4, hashlib.sha256(b"data").hexdigest())
        db.commit()
        replacement = assets.reserve(db, grant["key"], 8, "source")
        db.commit()
    assert totals(factory) == (4, 8)
    with factory() as db:
        with pytest.raises(HTTPException, match="derived_generation_stale"):
            assets.finish(db, grant["key"], grant["token"], 4, "old")
        db.rollback()
        assets.finish(db, grant["key"], replacement["token"], 6, "new")
        db.commit()
    assert totals(factory) == (6, 0)


def test_concurrent_users_cannot_overbook_or_double_claim(ledger, monkeypatch):
    factory, storage = ledger
    initialize(factory, storage)
    monkeypatch.setattr(settings, "derived_budget_bytes", 10)

    def claim(key):
        with factory() as db:
            try:
                result = assets.reserve(db, key, 7, "source")
                db.commit()
                return result
            except HTTPException as exc:
                db.rollback()
                return exc.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["derived/f/a.png", "derived/g/b.png"]))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert "derived_capacity_exceeded" in results
    assert totals(factory) == (0, 7)
    key = next(r["key"] for r in results if isinstance(r, dict))
    assert claim(key) == "derived_write_in_progress"


def test_storage_failure_retains_capacity_until_successful_delete(ledger):
    factory, storage = ledger
    initialize(factory, storage)
    now = datetime.now(UTC)
    grant, _ = write(factory, storage, "derived/f/page.png", now=now - timedelta(days=31))

    class Unavailable:
        def delete(self, key):
            raise OSError("storage offline")

    with factory() as db:
        assert assets.collect_batch(db, Unavailable(), now=now) == 0
    assert totals(factory) == (4, 0)
    assert storage.exists(grant["key"])
    with factory() as db:
        assert assets.collect_batch(db, storage, now=now + timedelta(seconds=settings.derived_write_lease_seconds + 1)) == 1
    assert totals(factory) == (0, 0)
    assert not storage.exists(grant["key"])


def test_crash_reservation_and_deleted_source_cannot_publish(ledger):
    factory, storage = ledger
    initialize(factory, storage)
    now = datetime.now(UTC)
    with factory() as db:
        grant = assets.reserve(db, "derived/f/partial.png", 16, "source", now=now)
        db.commit()
        file = db.get(FileObject, "f")
        file.purged_at = now
        db.commit()
        with pytest.raises(HTTPException, match="file_source_unavailable"):
            assets.finish(db, grant["key"], grant["token"], 10, "late", now=now)
        db.rollback()
    storage.put_bytes(grant["key"], b"partial")
    due = now + timedelta(seconds=settings.derived_write_lease_seconds + settings.derived_orphan_grace_seconds + 1)
    with factory() as db:
        assert assets.collect_batch(db, storage, now=due) == 1
    assert totals(factory) == (0, 0)
    assert not storage.exists(grant["key"])


def test_history_capture_and_transfer_pins_survive_cache_expiry(ledger):
    factory, storage = ledger
    initialize(factory, storage)
    now = datetime.now(UTC)
    capture, _ = write(factory, storage, "derived/f/captures/one", kind="capture", reconstructible=False)
    page, _ = write(factory, storage, "derived/f/page.png", now=now - timedelta(days=31))
    with factory() as db:
        assets.pin(db, capture["key"], "message:r", kind="message", run_id="r")
        assets.pin(db, page["key"], "download:one", kind="transfer", expires_at=now + timedelta(seconds=30))
        assets.retire(db, capture["key"], now=now - timedelta(days=2))
        db.commit()
        assert assets.collect_batch(db, storage, now=now) == 0
        assert assets.collect_batch(db, storage, now=now + timedelta(seconds=31)) == 1
        assets.unpin(db, "message:r")
        db.commit()
        assert assets.collect_batch(db, storage, now=now + timedelta(seconds=32)) == 1
    assert totals(factory) == (0, 0)


def test_backfill_cursor_is_bounded_durable_and_counts_all_bytes(ledger, monkeypatch):
    factory, storage = ledger
    monkeypatch.setattr(settings, "gc_batch_size", 2)
    for i in range(5):
        storage.put_bytes(f"derived/f/{i}.png", b"123")
    with factory() as db:
        assert assets.backfill_batch(db, storage) == 2
        db.commit()
    assert totals(factory) == (6, 0)
    # A new session represents process restart; no volatile cursor is needed.
    for _ in range(6):
        with factory() as db:
            assets.backfill_batch(db, storage)
            db.commit()
    assert totals(factory) == (15, 0)
    with factory() as db:
        assert db.get(DerivedBudget, 1).backfill_complete
        assert len(db.scalars(select(DerivedAsset)).all()) == 5
        assert assets.backfill_batch(db, storage) == 0


def test_over_reservation_publication_keeps_charge_and_cannot_be_pinned(ledger):
    factory, storage = ledger
    initialize(factory, storage)
    with factory() as db:
        grant = assets.reserve(db, "derived/f/a.png", 4, "source")
        db.commit()
        with pytest.raises(HTTPException, match="derived_reservation_exceeded"):
            assets.finish(db, grant["key"], grant["token"], 5, "too large")
        db.rollback()
        with pytest.raises(HTTPException, match="derived_asset_unavailable"):
            assets.pin(db, grant["key"], "bad", kind="message", run_id="r")
        db.rollback()
        assets.abandon(db, grant["key"], grant["token"])
        db.commit()
        assert assets.collect_batch(db, storage) == 1
    assert totals(factory) == (0, 0)


def test_staged_upload_is_not_published_until_business_transaction(ledger, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ailab_platform.api import routes_derived_assets as routes
    from ailab_platform.deps import get_db
    from ailab_platform.models import OutboxMessage

    factory, storage = ledger
    initialize(factory, storage)
    with factory.begin() as db:
        db.add(
            OutboxMessage(
                id="job",
                topic="file",
                payload={"file_id": "f", "kind": "file"},
                claim_token="attempt",
                claimed_until=datetime.now(UTC) + timedelta(minutes=3),
            )
        )
    app = FastAPI()
    app.include_router(routes.router)

    def sessions():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = sessions
    monkeypatch.setattr(routes, "get_storage", lambda: storage)
    agent_token = "agent-test-credential"
    monkeypatch.setattr(settings, "service_credentials", {"agent": "sha256:" + hashlib.sha256(agent_token.encode()).hexdigest()})
    headers = {"X-Service-Token": settings.service_token, "X-Run-Attempt": "attempt"}
    body = {"key": "derived/f/object.png", "version": "source", "max_bytes": 8, "job_id": "job"}
    with TestClient(app) as client:
        assert client.post("/internal/derived-assets/reserve", json=body, headers={"X-Service-Token": agent_token}).status_code == 403
        grant = client.post("/internal/derived-assets/reserve", json=body, headers=headers)
        assert grant.status_code == 200, grant.text
        token = grant.json()["token"]
        storage.put_bytes(body["key"], b"1234")
        completion = {"key": body["key"], "token": token}
        assert client.post("/internal/derived-assets/finish", json=completion, headers=headers).status_code == 200
        with factory() as db:
            assert db.get(DerivedAsset, body["key"]).state == "staged"
            assets.publish(db, body["key"], token)
            db.get(FileObject, "f").meta = {"scan": {"verdict": "clean"}, "published_key": body["key"]}
            db.commit()
        assert totals(factory) == (4, 0)
        with factory.begin() as db:
            db.get(OutboxMessage, "job").claim_token = "new attempt"
        assert client.post("/internal/derived-assets/finish", json=completion, headers=headers).status_code == 409
        with factory() as db:
            assert db.get(DerivedAsset, body["key"]).state == "ready"


def test_staged_bytes_lost_before_binding_are_collected_after_crash(ledger):
    factory, storage = ledger
    initialize(factory, storage)
    now = datetime.now(UTC)
    with factory() as db:
        grant = assets.reserve(db, "derived/f/staged.png", 8, "source", now=now)
        db.commit()
        storage.put_bytes(grant["key"], b"1234")
        assets.finish(db, grant["key"], grant["token"], 4, "digest", now=now, published=False)
        db.commit()
    assert totals(factory) == (4, 0)
    with factory() as db:
        assert assets.collect_batch(db, storage, now=now + timedelta(seconds=1100)) == 1
    assert totals(factory) == (0, 0)


def test_maintenance_selective_queries_use_indexes(ledger):
    factory, storage = ledger
    initialize(factory, storage)
    now = datetime.now(UTC)
    with factory() as db:
        db.bulk_insert_mappings(
            DerivedAsset,
            [
                {
                    "key": f"derived/f/{i:06d}",
                    "file_id": "f",
                    "source_version": "source",
                    "kind": "cache",
                    "state": "writing" if i < 5 else "ready",
                    "token": str(i),
                    "size_bytes": 0,
                    "reserved_bytes": 0,
                    "reconstructible": True,
                    "created_at": now,
                    "lease_until": now - timedelta(seconds=1),
                    "expires_at": now - timedelta(seconds=1) if i < 5 else now + timedelta(days=1),
                }
                for i in range(5000)
            ],
        )
        db.commit()
        db.execute(text("ANALYZE derived_assets"))
        for column, index in [("lease_until", "ix_derived_assets_state_lease"), ("expires_at", "ix_derived_assets_state_expiry")]:
            prefix = "EXPLAIN QUERY PLAN" if db.get_bind().dialect.name == "sqlite" else "EXPLAIN"
            state = "writing" if column == "lease_until" else "ready"
            result = db.execute(
                text(f"{prefix} SELECT key FROM derived_assets WHERE state=:state AND {column}<=:now ORDER BY {column}, key LIMIT 100"),
                {"state": state, "now": now},
            )
            plan = " ".join(str(row) for row in result).lower()
            assert index in plan, plan
        # Eviction walks rebuildable ready caches oldest first.
        prefix = "EXPLAIN QUERY PLAN" if db.get_bind().dialect.name == "sqlite" else "EXPLAIN"
        result = db.execute(
            text(
                f"{prefix} SELECT key FROM derived_assets WHERE state='ready' AND reconstructible=:yes AND kind='cache' "
                "AND created_at<=:now ORDER BY created_at, key LIMIT 100"
            ),
            {"yes": True, "now": now},
        )
        plan = " ".join(str(row) for row in result).lower()
        assert "ix_derived_assets_captures" in plan, plan


def test_last_capture_reference_release_registers_immediate_deletion(ledger):
    factory, storage = ledger
    initialize(factory, storage)
    grant, _ = write(factory, storage, "derived/f/captures/history", kind="capture", reconstructible=False)
    with factory() as db:
        assets.pin(db, grant["key"], "first", kind="message", run_id="r")
        assets.pin(db, grant["key"], "second", kind="checkpoint", run_id="r")
        db.commit()
        assets.unpin(db, "first")
        db.commit()
        assert assets.collect_batch(db, storage) == 0
        assets.unpin(db, "second")
        db.commit()
        assert assets.collect_batch(db, storage) == 1
    assert not storage.exists(grant["key"])
    assert totals(factory) == (0, 0)


def test_batch_capacity_is_atomic_and_stages_only_after_every_object_exists(ledger, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ailab_platform.api import routes_derived_assets as routes
    from ailab_platform.deps import get_db
    from ailab_platform.models import OutboxMessage

    factory, storage = ledger
    initialize(factory, storage)
    with factory.begin() as db:
        db.add(
            OutboxMessage(
                id="job",
                topic="file",
                payload={"file_id": "f", "kind": "file"},
                claim_token="attempt",
                claimed_until=datetime.now(UTC) + timedelta(minutes=3),
            )
        )
    app = FastAPI()
    app.include_router(routes.router)

    def sessions():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = sessions
    monkeypatch.setattr(routes, "get_storage", lambda: storage)
    headers = {"X-Service-Token": settings.service_token, "X-Run-Attempt": "attempt"}
    items = [
        {"key": f"derived/f/bundle/{name}", "version": "source", "max_bytes": 6, "kind": "native", "job_id": "job"}
        for name in ["manifest.json", "text.txt"]
    ]
    with TestClient(app, raise_server_exceptions=False) as client:
        monkeypatch.setattr(settings, "derived_budget_bytes", 10)
        refused = client.post("/internal/derived-assets/reserve-batch", json={"items": items}, headers=headers)
        assert refused.status_code == 507
        assert totals(factory) == (0, 0)
        with factory() as db:
            assert not db.scalars(select(DerivedAsset)).all()
        monkeypatch.setattr(settings, "derived_budget_bytes", 20)
        grant = client.post("/internal/derived-assets/reserve-batch", json={"items": items}, headers=headers)
        assert grant.status_code == 200, grant.text
        receipts = [{"key": item["key"], "token": item["token"]} for item in grant.json()["items"]]
        storage.put_bytes(items[0]["key"], b"{}")
        missing = client.post("/internal/derived-assets/finish-batch", json={"items": receipts}, headers=headers)
        assert missing.status_code == 500
        assert totals(factory) == (0, 12)
        storage.put_bytes(items[1]["key"], b"text")
        completed = client.post("/internal/derived-assets/finish-batch", json={"items": receipts}, headers=headers)
        assert completed.status_code == 200, completed.text
        assert totals(factory) == (6, 0)
        with factory() as db:
            assert {row.state for row in db.scalars(select(DerivedAsset))} == {"staged"}
        assert client.post("/internal/derived-assets/abandon-batch", json={"items": receipts}, headers=headers).status_code == 200
        with factory() as db:
            assert assets.collect_batch(db, storage) == 2
        assert totals(factory) == (0, 0)


def test_transport_discard_requires_missing_object_and_matching_generation(ledger, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ailab_platform.api import routes_derived_assets as routes
    from ailab_platform.deps import get_db

    factory, storage = ledger
    initialize(factory, storage)
    with factory() as db:
        grant = assets.reserve(db, "derived/f/transport.tar", 8, "source", kind="transfer")
        db.commit()
    app = FastAPI()
    app.include_router(routes.router)

    def sessions():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = sessions
    monkeypatch.setattr(routes, "get_storage", lambda: storage)
    headers = {"X-Service-Token": settings.service_token}
    with TestClient(app) as client:
        body = {"items": [{"key": grant["key"], "token": grant["token"]}]}
        storage.put_bytes(grant["key"], b"data")
        assert client.post("/internal/derived-assets/discard-batch", json=body, headers=headers).status_code == 409
        assert totals(factory) == (0, 8)
        storage.delete(grant["key"])
        stale = {"items": [{"key": grant["key"], "token": "wrong"}]}
        assert client.post("/internal/derived-assets/discard-batch", json=stale, headers=headers).status_code == 409
        assert totals(factory) == (0, 8)
        assert client.post("/internal/derived-assets/discard-batch", json=body, headers=headers).status_code == 200
        assert totals(factory) == (0, 0)
        assert client.post("/internal/derived-assets/discard-batch", json=body, headers=headers).status_code == 200


def test_transient_maintenance_reclaims_failed_writes_without_expiring_published_metadata(ledger):
    factory, storage = ledger
    initialize(factory, storage)
    now = datetime.now(UTC)
    with factory() as db:
        cached = assets.reserve(db, "derived/f/preview/page.png", 5, "source")
        db.commit()
        storage.put_bytes(cached["key"], b"image")
        ready = assets.finish(db, cached["key"], cached["token"], 5, "image-digest")
        ready.expires_at = now - timedelta(seconds=1)
        failed = assets.reserve(db, "derived/f/office-staging/failed.tar", 4, "source", kind="transfer")
        db.commit()
        storage.put_bytes(failed["key"], b"temp")
        assets.finish(db, failed["key"], failed["token"], 4, "temp-digest", published=False)
        assets.abandon(db, failed["key"], failed["token"])
        db.commit()
        assert assets.collect_batch(db, storage, now=now + timedelta(seconds=1), transient_only=True) == 1
        assert storage.exists(cached["key"]) and not storage.exists(failed["key"])
        assert db.get(DerivedAsset, cached["key"]).state == "ready"
        assert db.get(DerivedBudget, 1).used_bytes == 5
        assert db.get(DerivedBudget, 1).reserved_bytes == 0
