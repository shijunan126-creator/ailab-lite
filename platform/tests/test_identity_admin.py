"""RBAC, department SQL scopes, atomic audit, admin metadata contracts and quota resolution."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from ailab_platform import accounts
from ailab_platform.api.main import app
from ailab_platform.audit import record_audit
from ailab_platform.contracts.identity import Principal
from ailab_platform.db import SessionLocal, init_db
from ailab_platform.domain.authorization import Target, policy
from ailab_platform.domain.quota_policy import Limits, limits_for
from ailab_platform.models import AuditLog, Department, ModelUsage, QuotaPolicy, Run, User, UserRole

PASSWORD = " correct-password "


@pytest.fixture
def env():
    init_db()
    with SessionLocal() as db:
        depts = [Department(name=f"dept-{uuid.uuid4().hex[:8]}") for _ in range(2)]
        db.add_all(depts)
        db.flush()
        users = {}
        for role in ("user", "dept_admin", "platform_admin", "auditor", "other"):
            u = accounts.create_user(db, "u" + uuid.uuid4().hex[:12], PASSWORD)
            u.dept_id = depts[role == "other"].id
            u.role_rows = [UserRole(role=r) for r in sorted({"user", role if role != "other" else "user"})]
            token, _ = accounts.issue_session(db, u)
            users[role] = {"id": u.id, "headers": {"Authorization": "Bearer " + token}, "username": u.username}
        db.commit()
        yield {"users": users, "depts": [d.id for d in depts]}


def _headers(env, role):
    return env["users"][role]["headers"]


ACTIONS = [
    ("GET", "/users", None, "users.list"),
    ("POST", "/users", {"username": "new-user", "display_name": "New", "password": PASSWORD}, "users.create"),
    ("PATCH", "/users/{user}", {"display_name": "Changed"}, "users.update"),
    ("POST", "/users/{user}/password", {"password": PASSWORD}, "users.password"),
    ("GET", "/departments", None, "departments.list"),
    ("POST", "/departments", {"name": "New"}, "departments.create"),
    ("PATCH", "/departments/{dept}", {"name": "Changed"}, "departments.update"),
    ("DELETE", "/departments/{dept}", None, "departments.delete"),
    ("GET", "/quota-policies", None, "quota-policies.list"),
    (
        "POST",
        "/quota-policies",
        {"name": "New", "daily_tokens": 100, "monthly_tokens": 1000, "library_bytes": 123, "max_concurrent_runs": 1},
        "quota-policies.create",
    ),
    ("PATCH", "/quota-policies/{policy}", {"name": "Changed"}, "quota-policies.update"),
    ("DELETE", "/quota-policies/{policy}", None, "quota-policies.delete"),
    ("GET", "/audit", None, "audit.list"),
    ("GET", "/usage", None, "usage.list"),
]


@pytest.mark.parametrize("role", ["user", "dept_admin", "platform_admin", "auditor"])
@pytest.mark.parametrize("method,path,body,action", ACTIONS)
def test_every_admin_endpoint_obeys_policy(env, role, method, path, body, action):
    with SessionLocal() as db:
        p = QuotaPolicy(name="p", daily_tokens=1, monthly_tokens=2, library_bytes=3, max_concurrent_runs=1)
        empty = Department(name="empty")
        db.add_all([p, empty])
        db.commit()
        path = path.format(user=env["users"]["user"]["id"], dept=empty.id, policy=p.id)
    if method == "POST" and path == "/users":
        body = {**body, "username": "u" + uuid.uuid4().hex[:12]}
    expected = (
        role == "platform_admin"
        or (role == "auditor" and action in {"audit.list", "usage.list"})
        or (
            role == "dept_admin"
            and action
            in {"users.list", "users.create", "users.update", "users.password", "departments.list", "quota-policies.list", "usage.list"}
        )
    )
    with TestClient(app) as c:
        r = c.request(method, "/v1/admin" + path, json=body, headers=_headers(env, role))
    assert r.status_code < 300 if expected else r.status_code == 403, r.text
    if not expected:
        assert r.json() == {"detail": "forbidden"}


def test_dept_admin_query_scope_and_cannot_grant_global_roles(env):
    with TestClient(app) as c:
        headers = _headers(env, "dept_admin")
        result = c.get("/v1/admin/users", headers=headers).json()["items"]
        assert {u["id"] for u in result} == {env["users"][r]["id"] for r in ("user", "dept_admin", "platform_admin", "auditor")}
        assert c.get("/v1/admin/users", params={"dept_id": env["depts"][1]}, headers=headers).json()["items"] == []
        for target in ("other", "platform_admin", "auditor"):
            for suffix, body in (("", {"display_name": "no"}), ("/password", {"password": PASSWORD})):
                r = c.request(
                    "POST" if suffix else "PATCH", f"/v1/admin/users/{env['users'][target]['id']}{suffix}", json=body, headers=headers
                )
                assert r.status_code == 403
        target = env["users"]["user"]["id"]
        for values in ({"roles": ["platform_admin"]}, {"roles": ["auditor"]}, {"dept_id": env["depts"][1]}, {"dept_id": None}):
            assert c.patch(f"/v1/admin/users/{target}", json=values, headers=headers).status_code == 403
        assert c.patch(f"/v1/admin/users/{target}", json={"roles": ["dept_admin"]}, headers=headers).status_code == 200
        assert (
            c.post(
                "/v1/admin/users",
                json={"username": "u" + uuid.uuid4().hex[:10], "display_name": "x", "password": PASSWORD, "dept_id": env["depts"][1]},
                headers=headers,
            ).status_code
            == 403
        )
        depts = c.get("/v1/admin/departments", headers=headers).json()
        assert [d["id"] for d in depts] == [env["depts"][0]]


def test_metadata_contract_password_revocation_and_disabled(env):
    with TestClient(app) as c:
        admin = _headers(env, "platform_admin")
        r = c.get("/v1/me", headers=admin).json()
        assert r["can_admin"] and "platform_admin" in r["roles"] and r["dept"]["id"] == env["depts"][0]
        username = "u" + uuid.uuid4().hex[:12]
        r = c.post("/v1/admin/users", json={"username": username, "display_name": "Name", "password": PASSWORD}, headers=admin)
        assert r.status_code == 201, r.text
        row = r.json()
        assert set(row) == {
            "id",
            "username",
            "display_name",
            "email",
            "dept",
            "roles",
            "disabled",
            "quota_policy",
            "auth",
            "created_at",
            "last_login_at",
            "usage",
        }
        assert row["auth"] == "local" and row["created_at"].endswith(("Z", "+00:00"))
        token = c.post("/v1/auth/login", json={"username": username, "password": PASSWORD}).json()["token"]
        assert c.post(f"/v1/admin/users/{row['id']}/password", json={"password": "new-password"}, headers=admin).status_code == 204
        assert c.get("/v1/me", headers={"Authorization": "Bearer " + token}).status_code == 401
        assert c.patch(f"/v1/admin/users/{row['id']}", json={"disabled": True}, headers=admin).json()["disabled"]
        assert c.post("/v1/auth/login", json={"username": username, "password": "new-password"}).status_code == 401
        assert not c.patch(f"/v1/admin/users/{row['id']}", json={"disabled": False}, headers=admin).json()["disabled"]
        assert c.post("/v1/auth/login", json={"username": username, "password": "new-password"}).status_code == 200


def test_audit_and_change_are_atomic(env):
    uid = env["users"]["user"]["id"]

    def fail_audit(session, *_):
        if any(isinstance(row, AuditLog) and row.action == "users.update" for row in session.new):
            raise RuntimeError("forced audit write failure")

    event.listen(SessionLocal.class_, "before_flush", fail_audit)
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            assert (
                c.patch(
                    f"/v1/admin/users/{uid}", json={"display_name": "must rollback"}, headers=_headers(env, "platform_admin")
                ).status_code
                == 500
            )
    finally:
        event.remove(SessionLocal.class_, "before_flush", fail_audit)
    with SessionLocal() as db:
        assert db.get(User, uid).display_name != "must rollback"
        assert db.scalar(select(AuditLog).where(AuditLog.target_id == uid, AuditLog.action == "users.update")) is None


def test_keyset_users_audit_and_array_headers(env):
    with TestClient(app) as c:
        headers = _headers(env, "platform_admin")
        p1 = c.get("/v1/admin/users", params={"limit": 2}, headers=headers).json()
        p2 = c.get("/v1/admin/users", params={"limit": 2, "cursor": p1["next_cursor"]}, headers=headers).json()
        assert len(p1["items"]) == len(p2["items"]) == 2
        assert not {u["id"] for u in p1["items"]} & {u["id"] for u in p2["items"]}
        assert c.get("/v1/admin/users?limit=101", headers=headers).status_code == 422
        assert c.get("/v1/admin/users?cursor=garbage", headers=headers).status_code == 422
        audit_params = {"actor_id": env["users"]["platform_admin"]["id"], "action": "users.list", "limit": 1}
        a1 = c.get("/v1/admin/audit", params=audit_params, headers=headers).json()
        a2 = c.get("/v1/admin/audit", params={**audit_params, "cursor": a1["next_cursor"]}, headers=headers).json()
        assert a1["items"][0]["id"] != a2["items"][0]["id"]
        assert a1["items"][0]["at"].endswith(("Z", "+00:00"))
        d1 = c.get("/v1/admin/departments?limit=1", headers=headers)
        d2 = c.get("/v1/admin/departments", params={"limit": 1, "cursor": d1.headers["x-next-cursor"]}, headers=headers)
        assert d1.json()[0]["id"] != d2.json()[0]["id"]


def test_departments_policy_references_cycles_and_delete_conflicts(env):
    with TestClient(app) as c:
        headers = _headers(env, "platform_admin")
        values = {
            "name": "policy",
            "daily_tokens": 100,
            "monthly_tokens": 1000,
            "library_bytes": 123,
            "max_concurrent_runs": 1,
            "is_default": True,
        }
        p = c.post("/v1/admin/quota-policies", json=values, headers=headers).json()
        d = c.post(
            "/v1/admin/departments", json={"name": "child", "parent_id": env["depts"][0], "quota_policy_id": p["id"]}, headers=headers
        ).json()
        assert c.patch(f"/v1/admin/departments/{env['depts'][0]}", json={"parent_id": d["id"]}, headers=headers).status_code == 409
        assert c.delete(f"/v1/admin/departments/{env['depts'][0]}", headers=headers).status_code == 409
        assert c.delete(f"/v1/admin/quota-policies/{p['id']}", headers=headers).status_code == 409
        c.patch(f"/v1/admin/quota-policies/{p['id']}", json={"is_default": False}, headers=headers)
        assert c.delete(f"/v1/admin/quota-policies/{p['id']}", headers=headers).status_code == 409
        assert c.delete(f"/v1/admin/departments/{d['id']}", headers=headers).status_code == 204
        assert c.delete(f"/v1/admin/quota-policies/{p['id']}", headers=headers).status_code == 204


def test_usage_aggregation_dept_scope_all_groupings(env):
    with SessionLocal() as db:
        for role, amount in (("user", 10), ("other", 100)):
            run = Run(user_id=env["users"][role]["id"], title="secret-title", prompt="secret-prompt", model="model-test")
            db.add(run)
            db.flush()
            owner = db.get(User, run.user_id)
            # what the ledger writes: owner, department snapshot and the frozen amount (milli-units)
            db.add(
                ModelUsage(
                    run_id=run.id,
                    call_id=f"call-{role}",
                    user_id=owner.id,
                    dept_id=owner.dept_id,
                    model="model-test",
                    prompt_tokens=amount,
                    completion_tokens=amount,
                    weight=1,
                    weighted_amount=2 * amount * 1000,
                )
            )
        db.commit()
    with TestClient(app) as c:
        for group in ("dept", "user", "model"):
            data = c.get("/v1/admin/usage", params={"group_by": group}, headers=_headers(env, "dept_admin")).json()
            assert sum(row["weighted"] for row in data["rows"]) == 20
            assert "secret" not in str(data)
        data = c.get("/v1/admin/usage?group_by=model", headers=_headers(env, "auditor")).json()
        assert next(r for r in data["rows"] if r["key"] == "model-test")["weighted"] >= 220
        for role in ("platform_admin", "auditor", "dept_admin"):
            for path in ("/v1/runs", "/v1/conversations", "/v1/files"):
                r = c.get(path, headers=_headers(env, role))
                assert "secret-title" not in r.text and "secret-prompt" not in r.text


def test_policy_never_grants_content_access_and_handles_missing_department():
    for role in ("user", "dept_admin", "platform_admin", "auditor"):
        p = Principal("u", roles=frozenset({role}))
        for action in ("files.read", "runs.read", "conversations.read", "unknown"):
            assert not policy.can(p, action)
    assert not policy.can(Principal("u", roles=frozenset({"dept_admin"})), "users.list")
    assert not policy.can(Principal("u", dept_id="a", roles=frozenset({"dept_admin"})), "users.update", Target(dept_id="b"))


def test_limits_resolution_and_real_user_adapter(env):
    vals = [Limits(n, n, n, n) for n in (1, 2, 3, 4)]
    user = SimpleNamespace(
        quota_policy=vals[0], department=SimpleNamespace(quota_policy=vals[1]), default_quota_policy=vals[2], quota_fallback=vals[3]
    )
    assert limits_for(user) == vals[0]
    user.quota_policy = None
    assert limits_for(user) == vals[1]
    user.department.quota_policy = None
    assert limits_for(user) == vals[2]
    user.default_quota_policy = None
    assert limits_for(user) == vals[3]
    with SessionLocal() as db:
        u = db.get(User, env["users"]["user"]["id"])
        p = QuotaPolicy(name="user", daily_tokens=12, monthly_tokens=20, library_bytes=30, max_concurrent_runs=2)
        u.quota_policy = p
        db.add(p)
        db.flush()
        assert limits_for(u).daily_tokens == 12


def test_audit_redacts_secrets_and_bootstrap_is_idempotent(env, monkeypatch):
    monkeypatch.setattr(accounts.settings, "admin_username", env["users"]["user"]["username"])
    monkeypatch.setattr(accounts.settings, "admin_password", PASSWORD)
    with SessionLocal() as db:
        accounts.ensure_bootstrap_admin(db)
        db.flush()
        accounts.ensure_bootstrap_admin(db)
        record_audit(
            db,
            "test.redaction",
            "user",
            detail={"password": "secret", "token": "secret", "nested": {"access_token": "secret"}, "roles": ["user"]},
        )
        db.commit()
        assert [r.role for r in db.get(User, env["users"]["user"]["id"]).role_rows].count("platform_admin") == 1
        row = db.scalar(select(AuditLog).where(AuditLog.action == "test.redaction").order_by(AuditLog.at.desc()))
        assert row.detail == {"roles": ["user"]}


def test_insert_conflict_rolls_back_and_records_failed_admin_action(env, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from ailab_platform.api import routes_admin

    def conflict(*args, **kwargs):
        raise IntegrityError("INSERT", {}, Exception("unique conflict"))

    monkeypatch.setattr(routes_admin, "create_user", conflict)
    with TestClient(app) as c:
        r = c.post(
            "/v1/admin/users",
            json={"username": "race-user", "display_name": "New", "password": PASSWORD},
            headers=_headers(env, "platform_admin"),
        )
        assert r.status_code == 409 and r.json() == {"detail": "conflict"}
    with SessionLocal() as db:
        assert db.scalar(
            select(AuditLog).where(
                AuditLog.actor_id == env["users"]["platform_admin"]["id"], AuditLog.action == "users.create", AuditLog.outcome == "failure"
            )
        )


def test_usage_report_counts_purged_runs_and_the_department_at_call_time(env):
    """The report reads the ledger alone: a purged run's cost stays, and a later move keeps history where it was."""
    user_id, (old_dept, new_dept) = env["users"]["user"]["id"], env["depts"]
    with SessionLocal() as db:
        db.add(ModelUsage(run_id="purged-run", call_id="call-purged", user_id=user_id, dept_id=old_dept, model="m", weighted_amount=7_000))
        db.get(User, user_id).dept_id = new_dept
        db.commit()
    with TestClient(app) as c:
        rows = c.get("/v1/admin/usage", params={"group_by": "dept"}, headers=_headers(env, "auditor")).json()["rows"]
    assert {r["key"]: r["weighted"] for r in rows}.get(old_dept) == 7


def test_department_policy_governs_storage_and_concurrency_too(env):
    from ailab_platform.quota import enforce_library_quota, user_limits

    user_id = env["users"]["user"]["id"]
    with SessionLocal() as db:
        policy = QuotaPolicy(
            name=f"tight-{uuid.uuid4().hex[:6]}", daily_tokens=10, monthly_tokens=10, library_bytes=5, max_concurrent_runs=1
        )
        db.add(policy)
        db.flush()
        db.get(Department, env["depts"][0]).quota_policy_id = policy.id
        db.commit()
        limits = user_limits(db, user_id)
        assert (limits.library_bytes, limits.max_concurrent_runs) == (5, 1)
        with pytest.raises(HTTPException) as exc:
            enforce_library_quota(db, user_id, 6)
        assert exc.value.status_code == 413


def test_context_settings_platform_admin_only_and_audited(env):
    from ailab_platform.models import PlatformSetting

    with TestClient(app) as c:
        for role in ("user", "dept_admin", "auditor"):
            assert c.get("/v1/admin/settings/context", headers=_headers(env, role)).status_code == 403
            assert (
                c.put(
                    "/v1/admin/settings/context", headers=_headers(env, role), json={"auto_compact": True, "threshold_ratio": 0.7}
                ).status_code
                == 403
            )
        admin = _headers(env, "platform_admin")
        with SessionLocal() as db:
            row = db.get(PlatformSetting, "context")
            if row:
                db.delete(row)
                db.commit()
        initial = c.get("/v1/admin/settings/context", headers=admin).json()
        assert initial["auto_compact"] is False and initial["threshold_ratio"] == 0.5
        assert all(set(m) == {"id", "name", "context_tokens"} for m in initial["models"])
        for ratio in (0.19, 0.96):
            assert (
                c.put("/v1/admin/settings/context", headers=admin, json={"auto_compact": True, "threshold_ratio": ratio}).status_code == 422
            )
        changed = c.put("/v1/admin/settings/context", headers=admin, json={"auto_compact": True, "threshold_ratio": 0.7})
        assert changed.status_code == 200 and changed.json()["threshold_ratio"] == 0.7
        with SessionLocal() as db:
            assert db.scalar(select(AuditLog.id).where(AuditLog.action == "settings.context.update"))
            assert db.get(PlatformSetting, "context").value["auto_compact"] is True
            db.delete(db.get(PlatformSetting, "context"))
            db.commit()
