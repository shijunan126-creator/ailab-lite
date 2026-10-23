"""Black-box regression, runnable unchanged on the pre-T20 baseline."""

from fastapi.testclient import TestClient

from ailab_platform.api.main import app

from .test_identity_admin import env as env


def test_non_platform_admin_cannot_change_governance(env):
    with TestClient(app) as client:
        for role in ("user", "dept_admin", "auditor"):
            result = client.put(
                "/v1/skills/admin/policies", headers=env["users"][role]["headers"], json={"name": "pptx", "state": "disabled"}
            )
            assert result.status_code == 403


def test_admin_disable_applies_to_list_and_user_toggle(env):
    with TestClient(app) as client:
        admin = env["users"]["platform_admin"]["headers"]
        member = env["users"]["user"]["headers"]
        changed = client.put("/v1/skills/admin/policies", headers=admin, json={"name": "pptx", "state": "disabled"})
        assert changed.status_code == 200
        skill = next(s for s in client.get("/v1/skills", headers=member).json() if s["name"] == "pptx")
        assert skill["enabled"] is False
        assert client.put("/v1/skills/pptx", headers=member, json={"enabled": True}).status_code == 403
        client.put("/v1/skills/admin/policies", headers=admin, json={"name": "pptx", "state": "allowed"})
