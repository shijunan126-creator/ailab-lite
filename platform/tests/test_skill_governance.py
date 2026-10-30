"""T20 skill governance: manifest and index drift, sandbox attestation, and policy enforcement."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import ailab_platform.skill_index as skill_index_module
from ailab_platform.agent.prompts import skills
from ailab_platform.agent.skill_verification import SkillRefused, verify_sandbox_skills
from ailab_platform.agent.tools import ToolError, execute_tool
from ailab_platform.api.main import app
from ailab_platform.api.routes_skills import skill_input_for, usable_personal
from ailab_platform.broker import routes_skills as broker_skills
from ailab_platform.broker.routes_skills import sandbox_index, verify_lease_index
from ailab_platform.clients import ClientError
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, FileStatus, SkillPolicy, User, utcnow
from ailab_platform.skill_index import build_index, checked_index

from .test_identity_admin import env as env
from .test_model_gateway_context import SERVICE


@pytest.fixture
def skills_copy(tmp_path, monkeypatch):
    """A writable copy of the built-in skills, used as the skills directory for one test."""
    root = tmp_path / "skills"
    shutil.copytree(settings.skills_path, root)
    monkeypatch.setattr(settings, "skills_dir", root)
    skills.cache_clear()
    yield root
    skills.cache_clear()


@pytest.fixture
def attestations():
    broker_skills._attested.clear()
    yield
    broker_skills._attested.clear()


def fake_manager(*, provider="docker", ref="container-1", exit_code=0, stdout=None):
    calls = []
    index = checked_index(settings.skills_path)

    def exec_(lease_id, cmd, timeout):
        calls.append(lease_id)
        return SimpleNamespace(exit_code=exit_code, stdout=json.dumps(index) if stdout is None else stdout)

    manager = SimpleNamespace(
        provider=SimpleNamespace(name=provider),
        get=lambda lease_id: {"provider_ref": ref, "image": "office:test"},
        exec=exec_,
    )
    return manager, calls


def test_hash_detects_scripts_and_assets_and_symlinks(tmp_path):
    root = tmp_path / "skills"
    shutil.copytree(settings.skills_path, root)
    original = checked_index(root)
    target = root / "platform.xlsx" / "scripts" / "build_xlsx.py"
    target.write_bytes(target.read_bytes() + b"\n# drift\n")
    assert build_index(root) != original
    with pytest.raises(ValueError, match="mismatch"):
        checked_index(root)
    (root / "platform.xlsx" / "link").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="symlink"):
        build_index(root)


def test_manifest_requires_exact_directory_and_fields(tmp_path):
    root = tmp_path / "skills"
    shutil.copytree(settings.skills_path, root)
    manifest = root / "platform.xlsx" / "skill.toml"
    manifest.write_text(manifest.read_text().replace('name = "platform.xlsx"', 'name = "other"'))
    with pytest.raises(ValueError, match="name"):
        build_index(root)


def test_index_tool_exit_codes_separate_drift_from_an_invalid_manifest(skills_copy):
    script = Path(skill_index_module.__file__)

    def run(*args):
        return subprocess.run([sys.executable, str(script), str(skills_copy), *args], capture_output=True, text=True).returncode

    assert run("--check") == 0
    target = skills_copy / "platform.xlsx" / "scripts" / "build_xlsx.py"
    target.write_bytes(target.read_bytes() + b"\n# drift\n")
    assert run("--check") == 3
    manifest = skills_copy / "platform.xlsx" / "skill.toml"
    manifest.write_text(manifest.read_text().replace('name = "platform.xlsx"', 'name = "other"'))
    assert run("--check") == 2


def test_attestation_is_taken_once_per_lease_and_resource(attestations):
    manager, calls = fake_manager()
    result = verify_lease_index(manager, "lease-a")
    assert result["verified"] and result["provider_ref"] == "container-1" and result["image_ref"] == "office:test"
    verify_lease_index(manager, "lease-a")
    assert calls == ["lease-a"]
    manager.get = lambda lease_id: {"provider_ref": "container-2", "image": "office:test"}
    verify_lease_index(manager, "lease-a")
    assert calls == ["lease-a", "lease-a"]  # a replaced resource is attested again


def test_drift_is_refused_and_never_cached(attestations):
    manager, _ = fake_manager(exit_code=3, stdout="")
    with pytest.raises(HTTPException) as error:
        verify_lease_index(manager, "lease-b")
    assert error.value.status_code == 409 and error.value.detail == "skill_index_mismatch"
    assert not broker_skills._attested


def test_sandbox_index_that_differs_from_the_broker_source_is_refused(attestations):
    index = checked_index(settings.skills_path)
    index["skills"]["pptx"]["sha256"] = "0" * 64
    manager, _ = fake_manager(stdout=json.dumps(index))
    with pytest.raises(HTTPException) as error:
        verify_lease_index(manager, "lease-c")
    assert error.value.status_code == 409


def test_verifier_failure_is_unverified(attestations):
    manager, _ = fake_manager(exit_code=2, stdout="")
    with pytest.raises(HTTPException) as error:
        verify_lease_index(manager, "lease-d")
    assert error.value.status_code == 503 and error.value.detail == "skill_verification_failed"


def test_mock_sandbox_attests_the_host_directory_it_serves(attestations, monkeypatch, tmp_path):
    """No exec and no image: the index of the checkout the mock exposes, still checked against its manifests."""
    mock, calls = fake_manager(provider="mock")
    result = verify_lease_index(mock, "lease-e")
    assert result["verified"] and result["image_ref"] == "host-checkout" and calls == []
    drifted = tmp_path / "skills"
    shutil.copytree(settings.skills_path, drifted)
    target = drifted / "platform.xlsx" / "scripts" / "build_xlsx.py"
    target.write_bytes(target.read_bytes() + b"\n# drift\n")
    monkeypatch.setattr(broker_skills, "MOCK_SKILLS", drifted)
    broker_skills._attested.clear()
    with pytest.raises(HTTPException) as error:
        verify_lease_index(mock, "lease-f")
    assert error.value.status_code == 409


def test_sandbox_index_route_requires_a_lease_id():
    with pytest.raises(HTTPException) as error:
        sandbox_index(lease_id="")
    assert error.value.status_code == 422


def test_agent_refuses_an_attestation_not_bound_to_its_lease_and_index():
    index = checked_index(settings.skills_path)
    good = {"verified": True, "lease_id": "lease", "provider_ref": "container", "image_ref": "office:test", "index": index}

    class Broker:
        def __init__(self, body=None, error=None):
            self.body, self.error = body, error

        def _req(self, method, path, **kwargs):
            return kwargs

        async def _send(self, req):
            if self.error:
                raise self.error
            return httpx.Response(200, json=self.body)

    asyncio.run(verify_sandbox_skills(Broker(good), "lease"))
    for patch in (
        {"lease_id": "other"},
        {"verified": False},
        {"provider_ref": ""},
        {"image_ref": None},
        {"index": {**index, "skills": {}}},
    ):
        with pytest.raises(SkillRefused):
            asyncio.run(verify_sandbox_skills(Broker({**good, **patch}), "lease"))
    refused = ClientError("GET", "http://broker/internal/skills/index", 409, "skill_index_mismatch")
    with pytest.raises(SkillRefused):
        asyncio.run(verify_sandbox_skills(Broker(error=refused), "lease"))


def test_load_skill_tool_reports_a_refusal_as_a_tool_error():
    class Refusing:
        def _req(self, method, path, **kwargs):
            return kwargs

        async def _send(self, req):
            raise ClientError("GET", "http://broker/internal/skills/index", 409, "skill_index_mismatch")

    with pytest.raises(ToolError, match="已拒绝加载"):
        asyncio.run(execute_tool("load_skill", {"name": "pptx"}, "lease", Refusing()))


def test_global_deny_department_and_role_security(env):
    with TestClient(app) as client:
        admin = env["users"]["platform_admin"]["headers"]
        member = env["users"]["user"]["headers"]
        for role in ("user", "dept_admin", "auditor"):
            assert (
                client.put(
                    "/v1/skills/admin/policies", headers=env["users"][role]["headers"], json={"name": "pptx", "state": "disabled"}
                ).status_code
                == 403
            )
        assert client.put("/v1/skills/admin/policies", headers=admin, json={"name": "pptx", "state": "disabled"}).status_code == 200
        assert (
            client.put(
                "/v1/skills/admin/policies", headers=admin, json={"scope": env["depts"][0], "name": "pptx", "state": "allowed"}
            ).status_code
            == 200
        )
        assert not next(s for s in client.get("/v1/skills", headers=member).json() if s["name"] == "pptx")["enabled"]
        assert client.put("/v1/skills/pptx", headers=member, json={"enabled": True}).status_code == 403
        assert client.post("/v1/runs", headers=member, json={"prompt": "test", "skill": "pptx"}).status_code == 400
    with SessionLocal() as db:
        db.query(SkillPolicy).delete()
        db.commit()


def _personal_skill(db, owner_id: str, name: str) -> str:
    file = FileObject(
        user_id=owner_id,
        filename=f"{name}-{uuid.uuid4().hex[:8]}.md",
        purpose="skill",
        content_type="text/markdown",
        size=1,
        storage_key=f"test/{name}",
        status=FileStatus.READY,
        meta={"skill": {"name": name, "title": name, "description": "测试", "kind": "md"}},
    )
    db.add(file)
    db.commit()
    return file.id


def test_personal_skill_cannot_be_switched_on_while_its_policy_is_disabled(env):
    with SessionLocal() as db:
        _personal_skill(db, env["users"]["user"]["id"], "weekly")
        db.add(SkillPolicy(scope="global", name="*personal*", state="disabled"))
        db.commit()
    with TestClient(app) as client:
        member = env["users"]["user"]["headers"]
        assert client.put("/v1/skills/weekly", headers=member, json={"enabled": True}).status_code == 403
        assert client.put("/v1/skills/weekly", headers=member, json={"enabled": False}).status_code == 200
    with SessionLocal() as db:
        db.query(SkillPolicy).delete()
        db.commit()


def test_personal_review_approval_and_deleted_file(env):
    with SessionLocal() as db:
        owner = db.get(User, env["users"]["user"]["id"])
        fid = _personal_skill(db, owner.id, "weekly")
        db.add(SkillPolicy(scope="global", name="*personal*", state="review"))
        db.commit()
        assert "weekly" not in usable_personal(db, owner)
    with TestClient(app) as client:
        admin = env["users"]["platform_admin"]["headers"]
        page = client.get("/v1/skills/admin/reviews", headers=admin)
        assert page.status_code == 200 and any(s["file_id"] == fid for s in page.json()["items"])
        assert client.put(f"/v1/skills/admin/reviews/{fid}", headers=admin, json={"state": "allowed"}).status_code == 200
    with SessionLocal() as db:
        owner = db.get(User, env["users"]["user"]["id"])
        assert "weekly" in usable_personal(db, owner)
        db.get(SkillPolicy, ("global", "*personal*")).state = "disabled"
        db.commit()
        assert "weekly" not in usable_personal(db, owner)
        db.get(SkillPolicy, ("global", "*personal*")).state = "allowed"
        db.get(FileObject, fid).deleted_at = utcnow()
        db.commit()
        assert "weekly" not in usable_personal(db, owner)
        db.query(SkillPolicy).delete()
        db.commit()


def test_roles_are_enforced_in_shared_model_input(env, skills_copy):
    manifest = skills_copy / "pptx" / "skill.toml"
    manifest.write_text(manifest.read_text().replace("roles = []", 'roles = ["platform_admin"]'))
    (skills_copy / "index.json").write_text(json.dumps(build_index(skills_copy), ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    skills.cache_clear()
    with SessionLocal() as db:
        member = db.get(User, env["users"]["user"]["id"])
        admin = db.get(User, env["users"]["platform_admin"]["id"])
        assert "pptx" in skill_input_for(db, member)["disabled_skills"]
        assert "pptx" not in skill_input_for(db, admin)["disabled_skills"]


def test_legacy_bootstrap_and_model_input_apply_the_same_policy(env):
    with TestClient(app) as client:
        admin = env["users"]["platform_admin"]["headers"]
        member = env["users"]["user"]["headers"]
        run = client.post("/v1/runs", headers=member, json={"prompt": "policy"}).json()
        assert client.put("/v1/skills/admin/policies", headers=admin, json={"name": "pptx", "state": "disabled"}).status_code == 200
        for path in (f"/internal/runs/{run['id']}", f"/internal/runs/{run['id']}/model-input"):
            body = client.get(path, headers=SERVICE).json()
            assert "pptx" in body["disabled_skills"], path
    with SessionLocal() as db:
        db.query(SkillPolicy).delete()
        db.commit()


def test_repository_golden_rejects_corrupt_workbook(tmp_path):
    import importlib.util

    from openpyxl import load_workbook

    path = Path(__file__).parent / "golden/run.py"
    spec = importlib.util.spec_from_file_location("golden_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.self_test(tmp_path)
    book = load_workbook(tmp_path / "workbook-basic.xlsx")
    book["Data"]["D1"] = "#REF!"
    book.save(tmp_path / "workbook-basic.xlsx")
    with pytest.raises(AssertionError, match="Excel error"):
        module.check_outputs(tmp_path)
