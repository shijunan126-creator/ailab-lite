"""Frozen asset grants survive registry deletion until conversation retention."""

from datetime import timedelta

import pytest
from sqlalchemy import delete, select, text

from ailab_platform.api.asset_refs import register_assets
from ailab_platform.api.brand import save_brand, workspace_input
from ailab_platform.api.reaper import purge_deleted_conversations, purge_unused_assets, unused_asset_query
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, LifecycleDeletion, Run, RunAssetRef, Template, utcnow
from ailab_platform.quota import library_bytes
from ailab_platform.storage import get_storage
from tests.test_projects_brand import api as api
from tests.test_templates import file_id, ready


@pytest.mark.parametrize("scope", ["personal", "company"])
def test_deleted_template_retained_by_run_then_collected_after_retention(api, monkeypatch, scope):
    template = ready(api, scope=scope)
    fid = file_id(template)
    conv = api.post("/v1/conversations", json={}).json()
    run = api.post(f"/v1/conversations/{conv['id']}/messages", json={"prompt": "use", "template": {"id": template["id"]}}).json()
    assert api.post(f"/v1/runs/{run['id']}/cancel").status_code == 200
    api.delete(f"/v1/templates/{template['id']}")
    now = utcnow() + timedelta(seconds=settings.reaper_grace_seconds + 1)
    with SessionLocal() as db:
        assert db.scalar(select(RunAssetRef).where(RunAssetRef.run_id == run["id"], RunAssetRef.file_id == fid))
        purge_unused_assets(db, now)
        assert db.get(FileObject, fid).purged_at is None
    api.delete(f"/v1/conversations/{conv['id']}")
    monkeypatch.setattr(settings, "conversation_purge_days", 1)
    # No events left: exercise the actual retention hard-delete and FK cascade in one bounded pass.
    from ailab_platform.models import RunEvent

    with SessionLocal() as db:
        db.execute(delete(RunEvent).where(RunEvent.run_id == run["id"]))
        db.commit()
        purge_deleted_conversations(db, now + timedelta(days=2))
        assert db.get(Run, run["id"]) is None
        assert not db.scalar(select(RunAssetRef.id).where(RunAssetRef.run_id == run["id"]))
        owner = db.get(FileObject, fid).user_id
        before = library_bytes(db, owner)
        key = db.get(FileObject, fid).storage_key
        size = db.get(FileObject, fid).size
        purge_unused_assets(db, now + timedelta(days=2))
        db.expire_all()
        assert db.get(FileObject, fid).purged_at is not None
        assert db.get(Template, template["id"]) is not None
        assert library_bytes(db, owner) <= before - (size if scope == "personal" else 0)
        assert not get_storage().exists(key)
        assert db.scalar(select(LifecycleDeletion.id).where(LifecycleDeletion.key == f"derived/{fid}/"))


def test_asset_gc_grace_live_registry_orphan_and_batch_bound(api, monkeypatch):
    live = ready(api)
    retired = ready(api, name="retired.pptx")
    api.delete(f"/v1/templates/{retired['id']}")
    with SessionLocal() as db:
        original = db.get(FileObject, file_id(live))
        orphan = FileObject(user_id=original.user_id, filename="orphan", purpose="template", storage_key="unused-orphan")
        db.add(orphan)
        db.commit()
        oid = orphan.id
        now = utcnow()
        purge_unused_assets(db, now)
        assert db.get(FileObject, file_id(retired)).purged_at is None
        monkeypatch.setattr(settings, "gc_batch_size", 1)
        future = now + timedelta(seconds=settings.reaper_grace_seconds + 1)
        assert purge_unused_assets(db, future) <= 1
        purge_unused_assets(db, future)
        db.expire_all()
        assert db.get(FileObject, oid).purged_at is not None
        assert db.get(FileObject, file_id(retired)).purged_at is not None
        assert db.get(FileObject, file_id(live)).purged_at is None


def test_brand_freeze_ref_and_current_pending_protection(api):
    # Freeze through the same bootstrap path, then retire the pointer transactionally.
    conv = api.post("/v1/conversations", json={}).json()
    rid = api.post(f"/v1/conversations/{conv['id']}/messages", json={"prompt": "brand"}).json()["id"]
    with SessionLocal() as db:
        run = db.get(Run, rid)
        assets = [
            FileObject(user_id=run.user_id, purpose="brand", filename=f"brand-{i}", storage_key=f"unused-brand-{i}", status="ready")
            for i in range(3)
        ]
        db.add_all(assets)
        db.flush()
        ids = [f.id for f in assets]
        save_brand(db, {"enabled": True, "logo_file_id": ids[0], "pending": {"docx": ids[1]}})
        db.commit()
        workspace_input(db, run)
        assert db.scalar(
            select(RunAssetRef.id).where(RunAssetRef.run_id == rid, RunAssetRef.file_id == ids[0], RunAssetRef.kind == "brand")
        )
        now = utcnow() + timedelta(seconds=settings.reaper_grace_seconds + 1)
        purge_unused_assets(db, now)
        db.expire_all()
        assert all(db.get(FileObject, fid).purged_at is None for fid in ids[:2])
        assert db.get(FileObject, ids[2]).purged_at
        save_brand(db, {"enabled": True, "logo_file_id": ids[1]})
        db.commit()
        purge_unused_assets(db, now)
        db.expire_all()
        assert db.get(FileObject, ids[0]).purged_at is None
        db.execute(delete(RunAssetRef).where(RunAssetRef.run_id == rid))
        db.commit()
        purge_unused_assets(db, utcnow())  # replacement starts a fresh grace period
        db.expire_all()
        assert db.get(FileObject, ids[0]).purged_at is None
        purge_unused_assets(db, now)
        db.expire_all()
        assert db.get(FileObject, ids[0]).purged_at


def test_gc_rechecks_refs_under_lock(api, monkeypatch):
    from contextlib import contextmanager

    import ailab_platform.api.reaper as reaper

    template = ready(api)
    fid = file_id(template)
    api.delete(f"/v1/templates/{template['id']}")
    with SessionLocal() as db:
        owner = db.get(FileObject, fid).user_id
        run = Run(user_id=owner, prompt="frozen concurrently", model="m", options={"template": {"file_id": fid}})
        db.add(run)
        db.commit()
        original = reaper.user_critical_section

        @contextmanager
        def concurrent_ref(session, user_id):
            with original(session, user_id):
                register_assets(session, run)
                session.commit()
                yield

        monkeypatch.setattr(reaper, "user_critical_section", concurrent_ref)
        purge_unused_assets(db, utcnow() + timedelta(seconds=settings.reaper_grace_seconds + 1))
        db.expire_all()
        assert db.get(FileObject, fid).purged_at is None


def test_asset_candidate_query_uses_indexes(api):
    with SessionLocal() as db:
        sql = str(unused_asset_query(utcnow()).compile(db.bind, compile_kwargs={"literal_binds": True}))
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SET LOCAL enable_seqscan = off"))
            plan = str(db.execute(text("EXPLAIN " + sql)).all())
        else:
            plan = str(db.execute(text("EXPLAIN QUERY PLAN " + sql)).all())
        assert "ix_files_asset_gc" in plan and "ix_run_asset_refs_file" in plan and "ix_templates_file_id" in plan


def test_queued_template_retained_before_its_run_exists(api):
    template = ready(api)
    fid = file_id(template)
    conv = api.post("/v1/conversations", json={}).json()
    first = api.post(f"/v1/conversations/{conv['id']}/messages", json={"prompt": "without template"}).json()
    queued = api.post(f"/v1/conversations/{conv['id']}/queue", json={"prompt": "later", "template": {"id": template["id"]}})
    assert queued.status_code == 201, queued.text
    api.delete(f"/v1/templates/{template['id']}")
    with SessionLocal() as db:
        assert db.scalar(select(RunAssetRef.id).where(RunAssetRef.run_id == first["id"], RunAssetRef.file_id == fid))
        purge_unused_assets(db, utcnow() + timedelta(seconds=settings.reaper_grace_seconds + 1))
        db.expire_all()
        assert db.get(FileObject, fid).purged_at is None
