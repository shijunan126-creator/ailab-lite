"""Per-user usage for the simplified admin account view."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from ailab_platform import accounts
from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal, init_db
from ailab_platform.models import ModelUsage, UserRole, utcnow


def _user(c: TestClient, *, admin: bool = False) -> tuple[dict, str]:
    init_db()
    name = f"u{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        user = accounts.create_user(db, name, "usage-password")
        if admin:
            db.add(UserRole(user_id=user.id, role="platform_admin"))
        db.commit()
        user_id = user.id
    token = c.post("/v1/auth/login", json={"username": name, "password": "usage-password"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}, user_id


def test_admin_can_read_one_users_usage_but_user_cannot() -> None:
    with TestClient(app) as c:
        admin, _ = _user(c, admin=True)
        owner, owner_id = _user(c)
        with SessionLocal() as db:
            db.add(
                ModelUsage(
                    user_id=owner_id,
                    run_id=None,
                    model="test-fast",
                    status="succeeded",
                    prompt_tokens=100,
                    completion_tokens=20,
                    weighted_amount=1200,
                    created_at=utcnow(),
                )
            )
            db.commit()
        response = c.get(f"/v1/admin/users/{owner_id}/usage", headers=admin)
        assert response.status_code == 200
        body = response.json()
        assert body["total"]["input_tokens"] == 100
        assert body["total"]["output_tokens"] == 20
        assert body["total"]["calls"] == 1
        assert c.get(f"/v1/admin/users/{owner_id}/usage", headers=owner).status_code == 403
