"""Platform publication, following, frozen inputs and private customization contracts."""

import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.api.routes_internal import _bound_file
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, OutboxMessage, PlatformSkill, UserPlatformSkill
from ailab_platform.quota import library_bytes
from ailab_platform.service_auth import SERVICE_SCOPES, ServicePrincipal
from ailab_platform.storage import get_storage
from ailab_platform.worker.pipeline import process_file
from ailab_platform.worker.skill_unpack import process_skill_unpack

from .test_identity_admin import env as env
from .test_modes_skills_attachments import _ApiClient
from .test_personal_skills import SKILL_MD, _upload, _zip
from .test_skill_drafts import WorkerClient
from .test_skill_settings import SERVICE


@pytest.fixture
def setup(env):
    with TestClient(app) as c:
        admin = env["users"]["platform_admin"]["headers"]
        member = env["users"]["user"]["headers"]
        other = env["users"]["other"]["headers"]
        name = "platform-" + uuid.uuid4().hex[:8]
        response = c.post("/v1/admin/platform-skills", headers=admin, json={"name": name, "title": "周报"})
        assert response.status_code == 201, response.text
        skill = response.json()
        yield c, admin, member, other, skill, env
        c.delete(f"/v1/admin/platform-skills/{skill['id']}", headers=admin)
        # Other suites intentionally wipe files; a soft-deleted registry entry still owns its current-file FK.
        with SessionLocal() as db:
            db.delete(db.get(PlatformSkill, skill["id"]))
            db.commit()


def version(c, admin, skill, content=None, *, scan=True, zipped=False):
    data = (content or SKILL_MD.format(name=skill["name"])).encode()
    if zipped:
        data = _zip({"SKILL.md": data, "scripts/a.py": b"print(1)"})
    response = c.post(
        f"/v1/admin/platform-skills/{skill['id']}/versions", headers=admin, files={"upload": ("skill.zip" if zipped else "SKILL.md", data)}
    )
    assert response.status_code == 202, response.text
    fid = response.json()["file_id"]
    if scan:
        process_file(fid, platform=_ApiClient(c), storage=get_storage())
    return fid


def run_input(c, user, **body):
    response = c.post("/v1/runs", headers=user, json={"prompt": "test", **body})
    assert response.status_code == 201, response.text
    run = response.json()
    data = c.get(f"/internal/runs/{run['id']}/model-input", headers=SERVICE)
    assert data.status_code == 200, data.text
    return run, data.json()["personal_skills"]


def bound(file, run, service="agent", job="skill"):
    with SessionLocal() as db:
        return _bound_file(db, file, ServicePrincipal(service, SERVICE_SCOPES[service]), run["id"], job).id


def customize(c, member, skill):
    response = c.post(f"/v1/platform-skills/{skill['id']}/customize", headers=member)
    assert response.status_code == 202, response.text
    draft = response.json()
    saved = c.put(
        f"/v1/skill-drafts/{draft['id']}/files/SKILL.md",
        headers=member,
        json={"content": SKILL_MD.format(name=skill["name"]) + "\n自定义内容", "expected_revision": draft["revision"]},
    )
    assert saved.status_code == 200, saved.text
    confirmed = c.post(f"/v1/skill-drafts/{draft['id']}/confirm", headers=member, json={"expected_revision": saved.json()["revision"]})
    assert confirmed.status_code == 200, confirmed.text
    fid = confirmed.json()["file_id"]
    process_file(fid, platform=_ApiClient(c), storage=get_storage())
    return fid


def test_follow_updates_next_run_but_running_run_stays_pinned_and_unadded_user_cannot_read(setup):
    c, admin, member, other, skill, env = setup
    old = version(c, admin, skill)
    assert c.post(f"/v1/platform-skills/{skill['id']}/add", headers=member).status_code == 200
    run, inputs = run_input(c, member, skill=skill["name"])
    assert inputs[0]["file_id"] == old
    assert run["options"]["skills"][0]["kind"] == "platform"
    stranger, empty = run_input(c, other)
    assert not empty
    for service in ("agent", "broker"):
        assert bound(old, run, service) == old
        with pytest.raises(HTTPException):
            bound(old, stranger, service)
    assert c.get(f"/v1/files/{old}", headers=member).status_code == 404
    new = version(c, admin, skill, SKILL_MD.format(name=skill["name"]) + "\nversion 2")
    _, inputs = run_input(c, member, skills=[{"name": skill["name"], "file_id": old}])
    assert inputs[0]["file_id"] == new
    for endpoint in ("", "/model-input"):
        assert c.get(f"/internal/runs/{run['id']}{endpoint}", headers=SERVICE).json()["personal_skills"][0]["file_id"] == old
    assert bound(old, run) == old
    with pytest.raises(HTTPException):
        bound(new, run)
    assert c.delete(f"/v1/platform-skills/{skill['id']}/add", headers=member).status_code == 204
    with pytest.raises(HTTPException):
        bound(old, run)
    with SessionLocal() as db:
        assert library_bytes(db, env["users"]["user"]["id"]) == 0
        assert db.get(FileObject, new).purpose == "platform_skill"


def test_customized_copy_does_not_update_reset_restores_following(setup):
    c, admin, member, _other, skill, env = setup
    old = version(c, admin, skill)
    c.post(f"/v1/platform-skills/{skill['id']}/add", headers=member)
    copy = customize(c, member, skill)
    with SessionLocal() as db:
        sub = db.get(UserPlatformSkill, (env["users"]["user"]["id"], skill["id"]))
        assert sub.custom_lineage_id == db.get(FileObject, copy).lineage_id and sub.base_file_id == old
    new = version(c, admin, skill, SKILL_MD.format(name=skill["name"]) + "\nnew")
    row = next(s for s in c.get("/v1/skills", headers=member).json() if s["name"] == skill["name"])
    assert row["kind"] == "platform" and row["customized"] and row["update_available"]
    assert not row["error"]
    assert run_input(c, member)[1][0]["file_id"] == copy
    assert c.post(f"/v1/platform-skills/{skill['id']}/reset", headers=member).status_code == 200
    assert run_input(c, member)[1][0]["file_id"] == new
    with SessionLocal() as db:
        assert db.get(FileObject, copy) is None or db.get(FileObject, copy).purged_at


def test_admin_delete_detaches_custom_copies(setup):
    c, admin, member, other, skill, _env = setup
    version(c, admin, skill)
    for user in (member, other):
        c.post(f"/v1/platform-skills/{skill['id']}/add", headers=user)
    copy = customize(c, member, skill)
    assert c.delete(f"/v1/admin/platform-skills/{skill['id']}", headers=admin).status_code == 204
    listed = next(s for s in c.get("/v1/skills", headers=member).json() if s["name"] == skill["name"])
    assert listed["kind"] == "personal" and listed["file_id"] == copy and listed["enabled"]
    assert run_input(c, member)[1][0]["file_id"] == copy
    assert not run_input(c, other)[1]


def test_pending_bad_and_unpublished_versions_cannot_escape(setup):
    c, admin, member, _other, skill, _env = setup
    good = version(c, admin, skill)
    c.post(f"/v1/platform-skills/{skill['id']}/add", headers=member)
    pending = version(c, admin, skill, scan=False)
    run, inputs = run_input(c, member)
    assert inputs[0]["file_id"] == good
    assert c.post(f"/v1/admin/platform-skills/{skill['id']}/publish/{pending}", headers=admin).status_code == 422
    with pytest.raises(HTTPException):
        bound(pending, run)
    bad = version(c, admin, skill, SKILL_MD.format(name="wrong-name"))
    with SessionLocal() as db:
        assert db.get(PlatformSkill, skill["id"]).current_file_id == good
        assert not (db.get(FileObject, bad).meta or {}).get("platform_published")
    assert c.post(f"/v1/admin/platform-skills/{skill['id']}/publish/{bad}", headers=admin).status_code == 422
    assert c.post(f"/v1/admin/platform-skills/{skill['id']}/publish/{good}", headers=admin).status_code == 200


def test_platform_policies_and_user_switch_apply(setup):
    c, admin, member, _other, skill, env = setup
    version(c, admin, skill)
    c.post(f"/v1/platform-skills/{skill['id']}/add", headers=member)
    run, original = run_input(c, member)
    for scope in ("global", env["depts"][0]):
        url = "/v1/skills/admin/policies"
        assert c.put(url, headers=admin, json={"scope": scope, "name": skill["name"], "state": "disabled"}).status_code == 200
        assert not run_input(c, member)[1]
        with pytest.raises(HTTPException):
            bound(original[0]["file_id"], run)
        assert c.put(f"/v1/skills/{skill['name']}", headers=member, json={"enabled": True}).status_code == 403
        c.put(url, headers=admin, json={"scope": scope, "name": skill["name"], "state": "allowed"})
    assert c.put(f"/v1/skills/{skill['name']}", headers=member, json={"enabled": False}).status_code == 200
    assert not run_input(c, member)[1]


def test_namespace_admin_permissions_and_removed_market(setup):
    c, admin, member, _other, skill, _env = setup
    assert c.get("/v1/admin/platform-skills", headers=member).status_code == 403
    assert c.post("/v1/admin/platform-skills", headers=member, json={"name": "x", "title": "x"}).status_code == 403
    assert c.get("/v1/market", headers=member).status_code == 404
    for name in (skill["name"], "pptx", "auto-skill"):
        assert c.post("/v1/admin/platform-skills", headers=admin, json={"name": name, "title": "x"}).status_code == 422
    assert (
        c.post(
            "/v1/skills/content", headers=member, json={"name": skill["name"], "content": SKILL_MD.format(name=skill["name"])}
        ).status_code
        == 422
    )
    personal = "personal-" + uuid.uuid4().hex[:8]
    _upload(c, member, "SKILL.md", SKILL_MD.format(name=personal).encode())
    assert c.post("/v1/admin/platform-skills", headers=admin, json={"name": personal, "title": "x"}).status_code == 422
    collision = _upload(c, member, "SKILL.md", SKILL_MD.format(name=skill["name"]).encode())
    with SessionLocal() as db:
        assert "重名" in db.get(FileObject, collision).meta["skill_error"]


def test_zip_customization_uses_worker_unpack_and_keeps_assets(setup):
    c, admin, member, _other, skill, _env = setup
    version(c, admin, skill, zipped=True)
    c.post(f"/v1/platform-skills/{skill['id']}/add", headers=member)
    draft = c.post(f"/v1/platform-skills/{skill['id']}/customize", headers=member).json()
    assert draft["status"] == "preparing"
    with SessionLocal() as db:
        jid = db.scalar(select(OutboxMessage.id).where(OutboxMessage.payload["draft_id"].as_string() == draft["id"]))
    claim = c.post(f"/internal/jobs/{jid}/claim", headers=SERVICE, json={"consumer": "test"}).json()
    process_skill_unpack(WorkerClient(c), draft["id"], jid, claim["token"])
    draft = c.get(f"/v1/skill-drafts/{draft['id']}", headers=member).json()
    assert draft["status"] == "ready" and {f["path"] for f in draft["files"]} == {"SKILL.md", "scripts/a.py"}
    result = c.post(f"/v1/skill-drafts/{draft['id']}/confirm", headers=member, json={"expected_revision": draft["revision"]})
    assert result.status_code == 200, result.text
    fid = result.json()["file_id"]
    process_file(fid, platform=_ApiClient(c), storage=get_storage())
    assert run_input(c, member)[1][0]["file_id"] == fid


def test_pinned_versions_survive_retention_and_custom_drafts_are_fenced_by_reset(setup, monkeypatch):
    from ailab_platform.config import settings

    c, admin, member, _other, skill, _env = setup
    monkeypatch.setattr(settings, "max_file_versions", 1)
    old = version(c, admin, skill)
    c.post(f"/v1/platform-skills/{skill['id']}/add", headers=member)
    run, _ = run_input(c, member)
    draft = c.post(f"/v1/platform-skills/{skill['id']}/customize", headers=member).json()
    new = version(c, admin, skill, SKILL_MD.format(name=skill["name"]) + "\nnew")
    assert bound(old, run) == old
    with SessionLocal() as db:
        assert not db.get(FileObject, old).purged_at and not db.get(FileObject, new).purged_at
    c.post(f"/v1/platform-skills/{skill['id']}/reset", headers=member)
    assert (
        c.post(f"/v1/skill-drafts/{draft['id']}/confirm", headers=member, json={"expected_revision": draft["revision"]}).status_code == 409
    )
    assert run_input(c, member)[1][0]["file_id"] == new


def test_named_service_content_route_enforces_run_binding(setup, monkeypatch):
    import hashlib

    from ailab_platform.config import settings

    c, admin, member, other, skill, _env = setup
    monkeypatch.setattr(
        settings,
        "service_credentials",
        {
            "agent": "sha256:" + hashlib.sha256(b"platform-skill-agent").hexdigest(),
            "broker": "sha256:" + hashlib.sha256(b"platform-skill-broker").hexdigest(),
        },
    )
    file = version(c, admin, skill)
    c.post(f"/v1/platform-skills/{skill['id']}/add", headers=member)
    run, _ = run_input(c, member)
    stranger, _ = run_input(c, other)
    for service in ("agent", "broker"):
        headers = {"X-Service-Token": f"platform-skill-{service}"}
        url = f"/internal/files/{file}/content"
        good = c.get(url, params={"run_id": run["id"]}, headers=headers)
        assert good.status_code == 200 and good.content == SKILL_MD.format(name=skill["name"]).encode()
        assert c.get(url, params={"run_id": stranger["id"]}, headers=headers).status_code == 404
        assert c.get(url, headers=headers).status_code == 403
    assert c.get(f"/v1/admin/platform-skills/{skill['id']}/versions/{file}/content", headers=member).status_code == 403
    assert c.get(f"/v1/admin/platform-skills/{skill['id']}/versions/{file}/content", headers=admin).status_code == 200
    history = c.get(f"/v1/admin/platform-skills/{skill['id']}/versions", headers=admin).json()
    assert history["items"][0]["file_id"] == file and history["items"][0]["current"]


def test_customized_next_revision_and_personal_policy_do_not_disable_followers(setup):
    c, admin, member, _other, skill, _env = setup
    version(c, admin, skill)
    c.post(f"/v1/platform-skills/{skill['id']}/add", headers=member)
    copy = customize(c, member, skill)
    old_run, _ = run_input(c, member)
    newer = customize(c, member, skill)
    assert newer != copy and run_input(c, member)[1][0]["file_id"] == newer
    assert bound(copy, old_run, job="processing") == copy
    assert c.put("/v1/skills/admin/policies", headers=admin, json={"name": "*personal*", "state": "disabled"}).status_code == 200
    try:
        assert not run_input(c, member)[1]
        with pytest.raises(HTTPException):
            bound(copy, old_run)
        c.post(f"/v1/platform-skills/{skill['id']}/reset", headers=member)
        assert run_input(c, member)[1]
        assert c.put(f"/v1/skills/{skill['name']}", headers=member, json={"enabled": True}).status_code == 200
    finally:
        c.put("/v1/skills/admin/policies", headers=admin, json={"name": "*personal*", "state": "allowed"})


@pytest.mark.parametrize("zipped", [False, True])
def test_admin_online_draft_publishes_scanned_version_without_user_quota(setup, zipped):
    c, admin, member, _other, skill, env = setup
    old = version(c, admin, skill, zipped=zipped)
    c.post(f"/v1/platform-skills/{skill['id']}/add", headers=member)
    draft = c.post(f"/v1/admin/platform-skills/{skill['id']}/drafts", headers=admin).json()
    assert draft["origin"] == "platform_admin"
    if zipped:
        with SessionLocal() as db:
            jid = db.scalar(select(OutboxMessage.id).where(OutboxMessage.payload["draft_id"].as_string() == draft["id"]))
        claim = c.post(f"/internal/jobs/{jid}/claim", headers=SERVICE, json={"consumer": "test"}).json()
        assert claim["state"] == "claimed"
        process_skill_unpack(WorkerClient(c), draft["id"], jid, claim["token"])
        draft = c.get(f"/v1/skill-drafts/{draft['id']}", headers=admin).json()
    assert c.get(f"/v1/skill-drafts/{draft['id']}", headers=member).status_code == 404
    saved = c.put(
        f"/v1/skill-drafts/{draft['id']}/files/SKILL.md",
        headers=admin,
        json={"content": SKILL_MD.format(name=skill["name"]) + "\n平台新版本", "expected_revision": draft["revision"]},
    ).json()
    result = c.post(f"/v1/skill-drafts/{draft['id']}/confirm", headers=admin, json={"expected_revision": saved["revision"]})
    assert result.status_code == 200, result.text
    new = result.json()["file_id"]
    assert run_input(c, member)[1][0]["file_id"] == old
    process_file(new, platform=_ApiClient(c), storage=get_storage())
    assert run_input(c, member)[1][0]["file_id"] == new
    with SessionLocal() as db:
        assert db.get(FileObject, new).purpose == "platform_skill"
        assert library_bytes(db, env["users"]["platform_admin"]["id"]) == 0


def test_admin_can_edit_rolled_back_version_but_concurrent_upload_fences_draft(setup):
    c, admin, _member, _other, skill, _env = setup
    old = version(c, admin, skill)
    version(c, admin, skill, SKILL_MD.format(name=skill["name"]) + "\nnew")
    assert c.post(f"/v1/admin/platform-skills/{skill['id']}/publish/{old}", headers=admin).status_code == 200
    draft = c.post(f"/v1/admin/platform-skills/{skill['id']}/drafts", headers=admin).json()
    result = c.post(f"/v1/skill-drafts/{draft['id']}/confirm", headers=admin, json={"expected_revision": draft["revision"]})
    assert result.status_code == 200, result.text
    process_file(result.json()["file_id"], platform=_ApiClient(c), storage=get_storage())
    draft = c.post(f"/v1/admin/platform-skills/{skill['id']}/drafts", headers=admin).json()
    version(c, admin, skill, scan=False)
    assert (
        c.post(f"/v1/skill-drafts/{draft['id']}/confirm", headers=admin, json={"expected_revision": draft["revision"]}).status_code == 409
    )


def test_worker_redelivery_does_not_undo_explicit_rollback(setup):
    c, admin, _member, _other, skill, _env = setup
    old = version(c, admin, skill)
    new = version(c, admin, skill, SKILL_MD.format(name=skill["name"]) + "\nnew")
    c.post(f"/v1/admin/platform-skills/{skill['id']}/publish/{old}", headers=admin)
    with SessionLocal() as db:
        meta = dict(db.get(FileObject, new).meta)
    result = c.patch(f"/internal/files/{new}", headers=SERVICE, json={"meta": meta, "status": "ready"})
    assert result.status_code == 200, result.text
    with SessionLocal() as db:
        assert db.get(PlatformSkill, skill["id"]).current_file_id == old


def test_unreferenced_versions_are_trimmed_while_frozen_versions_survive(setup, monkeypatch):
    from ailab_platform.config import settings

    c, admin, member, _other, skill, _env = setup
    monkeypatch.setattr(settings, "max_file_versions", 1)
    frozen = version(c, admin, skill)
    c.post(f"/v1/platform-skills/{skill['id']}/add", headers=member)
    run, _ = run_input(c, member)
    unreferenced = version(c, admin, skill, SKILL_MD.format(name=skill["name"]) + "\nv2")
    version(c, admin, skill, SKILL_MD.format(name=skill["name"]) + "\nv3")
    current = version(c, admin, skill, SKILL_MD.format(name=skill["name"]) + "\nv4")
    assert bound(frozen, run) == frozen
    with SessionLocal() as db:
        assert db.get(FileObject, unreferenced).purged_at
        assert not db.get(FileObject, current).purged_at
