"""个人技能: skill files in a user's library are recognised, listed, switchable, callable - and only theirs."""

from __future__ import annotations

import asyncio
import io
import stat
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from ailab_platform import skillfiles
from ailab_platform.agent.loop import AgentLoop
from ailab_platform.api.main import app
from ailab_platform.llm import LLMResponse, ToolCall
from ailab_platform.storage import get_storage
from ailab_platform.worker.pipeline import process_file

from .test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM
from .test_modes_skills_attachments import _ApiClient
from .test_skill_settings import SERVICE, _user

SKILL_MD = "---\nname: {name}\ntitle: 周报助手\ndescription: 按团队模板写周报\n---\n# 步骤\n运行 scripts/fill.py 生成周报。\n"


def _zip(files: dict[str, bytes], symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


# ---- recognising a skill file --------------------------------------------------------------------


def test_a_skill_md_and_a_skill_zip_are_recognised() -> None:
    md = skillfiles.inspect(SKILL_MD.format(name="weekly").encode(), "SKILL.md")
    assert md == {"name": "weekly", "title": "周报助手", "description": "按团队模板写周报", "kind": "md", "files": 1}
    wrapped = _zip({"weekly/SKILL.md": SKILL_MD.format(name="weekly").encode(), "weekly/scripts/fill.py": b"print(1)"})
    assert skillfiles.inspect(wrapped, "weekly.zip")["files"] == 2
    text, files = skillfiles.unpack(wrapped, "weekly.zip")
    assert set(files) == {"SKILL.md", "scripts/fill.py"} and "scripts/fill.py" in text  # the folder is stripped


def test_ordinary_files_are_not_skills() -> None:
    assert skillfiles.inspect(b"# just notes\n", "notes.md") is None
    assert skillfiles.inspect(_zip({"a.txt": b"x"}), "photos.zip") is None
    assert skillfiles.inspect(b"not a zip", "x.zip") is None
    assert skillfiles.inspect(b"data", "report.pdf") is None


@pytest.mark.parametrize(
    "data,filename,message",
    [
        (b"---\nname: Bad Name\ndescription: x\n---\n", "SKILL.md", "不合规"),
        (b"---\nname: ok\n---\n", "SKILL.md", "description"),
        (_zip({"SKILL.md": SKILL_MD.format(name="a").encode(), "../evil.sh": b"x"}), "a.zip", "不安全的路径"),
        (_zip({"SKILL.md": SKILL_MD.format(name="a").encode()}, symlink="link"), "a.zip", "符号链接"),
        (_zip({"x/SKILL.md": b"x", "y/other.md": b"y"}), "a.zip", "没有 SKILL.md"),
    ],
)
def test_broken_skill_files_say_why(data, filename, message) -> None:
    with pytest.raises(skillfiles.SkillFileError, match=message):
        skillfiles.inspect(data, filename)


# ---- the library, the 技能 page, messages ------------------------------------------------------


def _upload(c: TestClient, headers: dict, filename: str, data: bytes) -> str:
    up = c.post("/v1/files", headers=headers, files={"upload": (filename, data, "application/octet-stream")}).json()
    process_file(up["id"], platform=_ApiClient(c), storage=get_storage())
    return up["id"]


def test_frozen_skill_read_requires_checkpoint_owner_and_current_policy(monkeypatch):
    from fastapi import HTTPException

    from ailab_platform.api.routes_internal import _bound_file
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import FileObject, SkillApproval, User
    from ailab_platform.service_auth import SERVICE_SCOPES, ServicePrincipal

    with TestClient(app) as c:
        me, other = _user(c), _user(c)
        file_id = _upload(c, me, "SKILL.md", SKILL_MD.format(name="frozen-weekly").encode())
        run = c.post("/v1/runs", headers=me, json={"prompt": "test"}).json()
        other_run = c.post("/v1/runs", headers=other, json={"prompt": "test"}).json()
        monkeypatch.setattr(
            "ailab_platform.api.routes_runtime.read_checkpoint",
            lambda db, run: {"state": {"loaded_skills": [{"name": "frozen-weekly", "file_id": file_id, "kind": "personal"}]}},
        )
        principal = ServicePrincipal("agent", SERVICE_SCOPES["agent"])
        with SessionLocal() as db:
            file = db.get(FileObject, file_id)
            file.is_head = 0
            db.commit()
            assert _bound_file(db, file_id, principal, run["id"], "skill").id == file_id
            with pytest.raises(HTTPException):
                _bound_file(db, file_id, principal, other_run["id"], "skill")
            db.add(SkillApproval(file_id=file_id, state="disabled"))
            db.commit()
            with pytest.raises(HTTPException):
                _bound_file(db, file_id, principal, run["id"], "skill")
            db.delete(db.get(SkillApproval, file_id))
            user = db.get(User, file.user_id)
            user.settings = {**(user.settings or {}), "disabled_skills": ["frozen-weekly"]}
            db.commit()
            with pytest.raises(HTTPException):
                _bound_file(db, file_id, principal, run["id"], "skill")


def test_a_skill_in_the_library_is_listed_switchable_and_usable_by_its_owner_only() -> None:
    name = f"weekly-{uuid.uuid4().hex[:6]}"
    with TestClient(app) as c:
        me, other = _user(c), _user(c)
        file_id = _upload(c, me, "SKILL.md", SKILL_MD.format(name=name).encode())
        mine = [s for s in c.get("/v1/skills", headers=me).json() if s["scope"] == "personal"]
        assert [(s["name"], s["title"], s["enabled"], s["file_id"]) for s in mine] == [(name, "周报助手", True, file_id)]
        assert [s for s in c.get("/v1/skills", headers=other).json() if s["scope"] == "personal"] == []

        run = c.post("/v1/runs", headers=me, json={"prompt": "写周报", "skill": name}).json()
        assert run["options"]["skills"] == [{"name": name, "title": "周报助手", "kind": "personal", "file_id": file_id, "at": None}]
        personal = c.get(f"/internal/runs/{run['id']}", headers=SERVICE).json()["personal_skills"]
        assert personal == [
            {"name": name, "title": "周报助手", "description": "按团队模板写周报", "file_id": file_id, "filename": "SKILL.md", "kind": "md"}
        ]
        assert c.post("/v1/runs", headers=other, json={"prompt": "x", "skill": name}).status_code == 400

        assert c.put(f"/v1/skills/{name}", headers=me, json={"enabled": False}).json() == {
            "name": name,
            "enabled": False,
            "scope": "personal",
        }
        assert c.post("/v1/runs", headers=me, json={"prompt": "x", "skill": name}).status_code == 400
        c.put(f"/v1/skills/{name}", headers=me, json={"enabled": True})
        assert c.delete(f"/v1/files/{file_id}", headers=me).status_code == 204  # deleting the file deletes the skill
        assert [s for s in c.get("/v1/skills", headers=me).json() if s["scope"] == "personal"] == []


def test_name_clashes_and_bad_files_are_shown_with_the_reason() -> None:
    name = f"dup-{uuid.uuid4().hex[:6]}"
    with TestClient(app) as c:
        me = _user(c)
        _upload(c, me, "pptx.md", SKILL_MD.format(name="pptx").encode())
        old = _upload(c, me, "v1.md", SKILL_MD.format(name=name).encode())
        new = _upload(c, me, "v2.md", SKILL_MD.format(name=name).encode())
        _upload(c, me, "bad.md", b"---\nname: BAD\ndescription: x\n---\n")
        personal = {s["file_id"]: s for s in c.get("/v1/skills", headers=me).json() if s["scope"] == "personal"}
        assert personal[new]["enabled"] and not personal[new]["error"]
        assert "更新的同名" in personal[old]["error"]
        errors = [s["error"] for s in personal.values() if s["error"]]
        assert any("通用技能" in e for e in errors) and any("不合规" in e for e in errors)


# ---- the agent ----------------------------------------------------------------------------------


class _Platform(FakePlatform):
    def __init__(self, files: dict[str, bytes], **run) -> None:
        super().__init__()
        self.files, self.extra = files, run

    async def get_run(self, run_id):
        return {**(await super().get_run(run_id)), **self.extra}

    async def get_file_bytes(self, file_id, **context):
        return self.files[file_id]


SKILL = {"name": "weekly", "title": "周报助手", "description": "按团队模板写周报", "file_id": "f1", "filename": "weekly.zip", "kind": "zip"}
ZIP = _zip({"SKILL.md": SKILL_MD.format(name="weekly").encode(), "scripts/fill.py": b"print('ok')"})


def test_the_model_sees_personal_skills_and_reads_one_whose_files_reach_the_sandbox() -> None:
    platform = _Platform({"f1": ZIP}, personal_skills=[SKILL])
    read = ToolCall(id="c1", name="load_skill", arguments={"name": "weekly"})
    run = ToolCall(id="c2", name="exec", arguments={"cmd": "python /workspace/.ailab/skills/weekly/scripts/fill.py"})
    llm = ScriptedLLM([LLMResponse(tool_calls=[read]), LLMResponse(tool_calls=[run]), LLMResponse(content="done")])
    broker = FakeBroker()
    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt_id="att_1"))
    system = llm.seen_messages[0][0]["content"]
    assert "- weekly：周报助手" not in system
    tool_msg = llm.seen_messages[1][-1]["content"]
    assert "运行 scripts/fill.py" in tool_msg and "/workspace/.ailab/skills/weekly/" in tool_msg
    # read before there was a sandbox: installed once the exec leased one
    assert sorted(p for p, _ in broker.put_calls if not p.startswith("/workspace/.ailab-")) == [
        "/workspace/.ailab/skills/weekly/SKILL.md",
        "/workspace/.ailab/skills/weekly/scripts/fill.py",
    ]


def test_a_picked_personal_skill_comes_preloaded() -> None:
    platform = _Platform({"f1": ZIP}, personal_skills=[SKILL], options={"mode": "work", "skill": "weekly"})
    llm = ScriptedLLM([LLMResponse(content="ok")])
    asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=llm).process("run_1", attempt_id="att_1"))
    system = llm.seen_messages[0][0]["content"]
    assert "运行 scripts/fill.py" not in system
    assert any(m["role"] == "tool" and "运行 scripts/fill.py" in m["content"] for m in llm.seen_messages[0])
    assert any(t == "agent.skill" and p["name"] == "weekly" and p["status"] == "loaded" for t, p in platform.events)


def test_selected_file_id_follows_personal_skill_lineage_head():
    name = f"weekly-{uuid.uuid4().hex[:6]}"
    with TestClient(app) as client:
        me = _user(client)
        old = _upload(client, me, "SKILL.md", SKILL_MD.format(name=name).encode())
        updated = client.post(
            "/v1/skills/content", headers=me, json={"name": name, "content": SKILL_MD.format(name=name), "expected_file_id": old}
        )
        assert updated.status_code == 201, updated.text
        current = updated.json()["file_id"]
        process_file(current, platform=_ApiClient(client), storage=get_storage())
        response = client.post(
            "/v1/runs", headers=me, json={"prompt": "写周报", "mode": "chat", "skills": [{"name": "旧标题", "file_id": old}]}
        )
        assert response.status_code == 201, response.text
        selected = response.json()["options"]["skills"]
        assert selected == [{"name": name, "title": "周报助手", "kind": "personal", "file_id": current, "at": None}]
