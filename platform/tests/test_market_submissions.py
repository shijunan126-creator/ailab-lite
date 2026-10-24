"""Marketplace submissions: immutable snapshot, admin review, and install."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from ailab_platform import accounts
from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal, init_db
from ailab_platform.library import assign_file_version
from ailab_platform.models import FileObject, FileStatus, UserRole, new_id
from ailab_platform.storage import get_storage


def _user(c: TestClient, *, admin: bool = False) -> tuple[dict, str]:
    init_db()
    name = f"m{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        user = accounts.create_user(db, name, "market-password")
        if admin:
            db.add(UserRole(user_id=user.id, role="platform_admin"))
        db.commit()
        user_id = user.id
    token = c.post("/v1/auth/login", json={"username": name, "password": "market-password"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}, user_id


def _skill(db, user_id: str, name: str = "community-demo") -> str:
    content = f"""---
name: {name}
title: 社区技能
description: 一个用于测试的技能
---

# 怎么做
写一句话。
""".encode()
    file_id = new_id("fil")
    key = f"skills/{user_id}/{file_id}/SKILL.md"
    get_storage().put_bytes(key, content, "text/markdown")
    row = FileObject(
        id=file_id,
        user_id=user_id,
        filename="SKILL.md",
        content_type="text/markdown",
        size=len(content),
        sha256="",
        storage_key=key,
        status=FileStatus.READY,
        purpose="skill",
        meta={"skill": {"name": name, "title": "社区技能", "description": "一个用于测试的技能", "kind": "md"}},
    )
    assign_file_version(db, row, None)
    db.add(row)
    db.commit()
    return file_id


def test_submit_review_publish_and_install() -> None:
    with TestClient(app) as c:
        author, author_id = _user(c)
        admin, _ = _user(c, admin=True)
        with SessionLocal() as db:
            file_id = _skill(db, author_id)

        submitted = c.post(
            "/v1/market/submissions",
            headers=author,
            json={
                "file_id": file_id,
                "title": "社区技能",
                "description": "一个用于测试的技能",
                "category": "其他",
                "license": "MIT",
                "version": "1.0.0",
                "changelog": "首次提交",
            },
        )
        assert submitted.status_code == 201
        listing_id = submitted.json()["listing_id"]

        pending = c.get("/v1/market/admin/submissions", headers=admin).json()
        assert [row["listing_id"] for row in pending] == [listing_id]
        detail = c.get(f"/v1/market/admin/submissions/{listing_id}", headers=admin).json()
        assert "SKILL.md" in detail["files"] and "怎么做" in detail["skill_md"]

        approved = c.post(
            f"/v1/market/admin/submissions/{listing_id}/review",
            headers=admin,
            json={"decision": "approved", "reason": ""},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "published"
        assert any(item["slug"] == "community-demo" for item in c.get("/v1/market/skills", headers=author).json())

        downloader, _ = _user(c)
        installed = c.post("/v1/market/skills/community-demo/install", headers=downloader)
        assert installed.status_code == 201
        assert installed.json()["status"] == "pending"


def test_submitter_cannot_review_and_rejection_is_visible() -> None:
    with TestClient(app) as c:
        author, author_id = _user(c, admin=True)
        with SessionLocal() as db:
            file_id = _skill(db, author_id, "community-self")
        listing = c.post(
            "/v1/market/submissions",
            headers=author,
            json={
                "file_id": file_id,
                "title": "自己的技能",
                "description": "测试自审",
                "category": "其他",
                "license": "MIT",
            },
        ).json()
        denied = c.post(
            f"/v1/market/admin/submissions/{listing['listing_id']}/review",
            headers=author,
            json={"decision": "approved", "reason": ""},
        )
        assert denied.status_code == 409
        other_admin, _ = _user(c, admin=True)
        rejected = c.post(
            f"/v1/market/admin/submissions/{listing['listing_id']}/review",
            headers=other_admin,
            json={"decision": "rejected", "reason": "说明不够清楚"},
        )
        assert rejected.status_code == 200
        mine = c.get("/v1/market/submissions/mine", headers=author).json()
        assert mine[0]["status"] == "rejected"
        assert mine[0]["reason"] == "说明不够清楚"
