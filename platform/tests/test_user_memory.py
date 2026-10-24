"""Account isolation, CAS, replay and Chat/Work memory tool contracts."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from ailab_platform.agent.loop import AgentLoop
from ailab_platform.agent.prompts import memory_prompt
from ailab_platform.api.main import app
from ailab_platform.llm import LLMResponse, ToolCall
from ailab_platform.storage import get_storage
from ailab_platform.worker.pipeline import process_file

from .run_claims import attempt_headers
from .test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM
from .test_modes_skills_attachments import _ApiClient
from .test_net_tools import SKILL_MD, _running
from .test_skill_settings import SERVICE, _user


def _put(c, me, revision=0, **changes):
    body = {
        "expected_revision": revision,
        "enabled": True,
        "auto_save": True,
        "entries": [{"kind": "user", "content": "喜欢简洁中文"}],
        **changes,
    }
    return c.put("/v1/memory", headers=me, json=body)


def _tool(c, run, action="add", revision=0, delivery="m1", **changes):
    return c.post(
        f"/internal/runs/{run}/memory",
        headers={**attempt_headers(c, run), "X-Delivery-Id": f"{run}:{delivery}"},
        json={"action": action, "expected_revision": revision, "content": "长期项目使用 Python", **changes},
    )


def test_memory_is_account_owned_atomic_and_does_not_clobber_other_settings():
    with TestClient(app) as c:
        me, other = _user(c), _user(c)
        assert c.get("/v1/memory").status_code == 401
        assert c.get("/v1/memory", headers=me).json()["revision"] == 0
        c.put("/v1/skills/pptx", headers=me, json={"enabled": False})
        saved = _put(c, me)
        assert saved.status_code == 200
        entry = saved.json()["entries"][0]
        assert entry["source"] == "user" and entry["updated_at"].endswith("+00:00")
        assert _put(c, me).status_code == 422
        assert not c.get("/v1/memory", headers=other).json()["entries"]
        assert next(s for s in c.get("/v1/skills", headers=me).json() if s["name"] == "pptx")["enabled"] is False
        # Another settings write must retain memory.
        c.put("/v1/skills/pptx", headers=me, json={"enabled": True})
        assert c.get("/v1/memory", headers=me).json()["entries"] == [entry]
        assert _put(c, me, 1, entries=[{**{k: entry[k] for k in ("id", "kind", "content")}, "content": "偏好英文"}]).status_code == 200
        assert _put(c, me, 2, entries=[]).json()["entries"] == []


@pytest.mark.parametrize(
    "entries",
    [
        [{"content": " "}],
        [{"content": "x" * 1001}],
        [{"content": "x"}] * 101,
        [{"content": "x" * 1000}] * 13,
        [{"id": "same", "content": "a"}] * 2,
    ],
)
def test_memory_limits_reject_before_writing(entries):
    with TestClient(app) as c:
        me = _user(c)
        assert _put(c, me, entries=entries).status_code == 422
        assert c.get("/v1/memory", headers=me).json()["revision"] == 0


def test_agent_memory_replay_conflict_and_fence():
    with TestClient(app) as c:
        me, other = _user(c), _user(c)
        run = _running(c, me)
        added = _tool(c, run)
        assert added.status_code == 200 and added.json()["revision"] == 1
        assert _tool(c, run).json() == added.json()  # committed write, lost response
        assert _tool(c, run, delivery="stale").status_code == 422
        entry = added.json()["entries"][0]
        assert entry["source"] == "agent"
        assert _tool(c, _running(c, other), action="remove", id=entry["id"]).status_code == 422
        assert _tool(c, run, action="replace", revision=1, delivery="m2", id=entry["id"], content="改用 TypeScript").status_code == 200
        assert _tool(c, run, action="remove", revision=2, delivery="m3", id=entry["id"]).json()["entries"] == []
        assert c.post(f"/internal/runs/{run}/memory", headers=me, json={"action": "list"}).status_code == 403
        assert (
            c.post(
                f"/internal/runs/{run}/memory",
                headers={**SERVICE, "X-Run-Attempt": "stale"},
                json={"action": "add", "content": "x", "expected_revision": 3},
            ).status_code
            == 409
        )
        assert _tool(c, run, delivery="empty", content=" ", revision=3).status_code == 422


def test_memory_controls_and_next_run_snapshot():
    with TestClient(app) as c:
        me = _user(c)
        _put(c, me, auto_save=False)
        run = _running(c, me)
        assert _tool(c, run, revision=1).status_code == 422
        assert _tool(c, run, revision=1, explicit=True).status_code == 200
        snapshot = c.get(f"/internal/runs/{run}/model-input", headers=SERVICE).json()["memory"]
        assert len(snapshot["entries"]) == 2 and snapshot["auto_save"] is False
        _put(c, me, 2, enabled=False)
        assert _tool(c, run, revision=3, delivery="disabled", explicit=True).status_code == 422
        assert _tool(c, run, action="list").json()["entries"] == []
        assert c.get(f"/internal/runs/{run}/model-input", headers=SERVICE).json()["memory"]["entries"] == []
        assert "喜欢简洁中文" not in memory_prompt(c.get("/v1/memory", headers=me).json())


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_memory_tool_in_both_modes_needs_no_sandbox(mode):
    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "options": {"mode": mode}, "memory": {"enabled": True, "revision": 0, "entries": []}}

        async def memory(self, run_id, payload):
            self.written = payload
            return {"revision": 1, "entries": [{"content": payload["content"]}]}

    platform, broker = Platform(), FakeBroker()
    call = ToolCall(id="m", name="memory", arguments={"action": "add", "content": "喜欢中文", "expected_revision": 0})
    llm = ScriptedLLM([LLMResponse(tool_calls=[call]), LLMResponse(content="已记住")])
    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt_id="att_1"))
    assert platform.written["content"] == "喜欢中文"
    assert broker.leases == 0


def test_skill_editor_scans_versions_conflicts_and_ownership():
    name = "editor-test"
    with TestClient(app) as c:
        me, other = _user(c), _user(c)
        body = {"name": name, "content": SKILL_MD.format(name=name)}
        created = c.post("/v1/skills/content", headers=me, json=body)
        assert created.status_code == 201
        fid = created.json()["file_id"]
        assert c.get(f"/v1/skills/content/{name}", headers=me).status_code in (404, 422)
        process_file(fid, platform=_ApiClient(c), storage=get_storage())
        content = c.get(f"/v1/skills/content/{name}", headers=me).json()
        assert content["content"] == body["content"] and content["editable"]
        assert c.get(f"/v1/skills/content/{name}", headers=other).status_code == 404
        assert c.post("/v1/skills/content", headers=other, json={**body, "expected_file_id": fid}).status_code == 422
        assert c.post("/v1/skills/content", headers=me, json=body).status_code == 422
        update = c.post("/v1/skills/content", headers=me, json={**body, "expected_file_id": fid, "content": body["content"] + "\n多加一步"})
        assert update.status_code == 201 and update.json()["file_id"] != fid
        assert c.post("/v1/skills/content", headers=me, json={**body, "expected_file_id": fid}).status_code == 422
        assert c.get("/v1/skills/content/pptx", headers=me).json()["editable"] is False
        assert c.post("/v1/skills/content", headers=me, json={"name": "pptx", "content": SKILL_MD.format(name="pptx")}).status_code == 422


def test_chat_can_read_builtin_skill_without_sandbox():
    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "options": {"mode": "chat"}}

    broker, platform = FakeBroker(), Platform()
    call = ToolCall(id="s", name="load_skill", arguments={"name": "pptx"})
    llm = ScriptedLLM([LLMResponse(tool_calls=[call]), LLMResponse(content="已读取")])
    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt_id="att_1"))
    assert next(p for t, p in platform.events if t == "agent.tool_result")["ok"]
    assert broker.leases == 0


def test_package_replacement_cas_and_no_script_loss():
    from .test_personal_skills import _zip

    with TestClient(app) as c:
        me = _user(c)
        name = "package-cas"
        data = _zip({"SKILL.md": SKILL_MD.format(name=name).encode(), "scripts/fill.py": b"print(1)"})
        first = c.post("/v1/files?purpose=skill", headers=me, files={"upload": ("skill.zip", data)}).json()
        fid = first["id"]
        assert c.post(f"/v1/files?purpose=skill&parent_id={fid}", headers=me, files={"upload": ("skill.zip", data)}).status_code == 422
        process_file(fid, platform=_ApiClient(c), storage=get_storage())
        assert c.get(f"/v1/skills/content/{name}", headers=me).json()["kind"] == "zip"
        assert (
            c.post(
                f"/v1/files?purpose=skill&parent_id={fid}", headers=me, files={"upload": ("SKILL.md", SKILL_MD.format(name=name).encode())}
            ).status_code
            == 422
        )
        second = c.post(f"/v1/files?purpose=skill&parent_id={fid}", headers=me, files={"upload": ("skill.zip", data)})
        assert second.status_code == 201
        assert c.post(f"/v1/files?purpose=skill&parent_id={fid}", headers=me, files={"upload": ("skill.zip", data)}).status_code == 422
        process_file(second.json()["id"], platform=_ApiClient(c), storage=get_storage())
        assert next(s for s in c.get("/v1/skills", headers=me).json() if s["name"] == name)["files"] == 2


def test_chat_skill_path_cannot_start_a_sandbox():
    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "options": {"mode": "chat"}}

    broker, platform = FakeBroker(), Platform()
    call = ToolCall(id="s", name="save_skill", arguments={"name": "notes", "mode": "create", "path": "/workspace/notes.zip"})
    llm = ScriptedLLM([LLMResponse(tool_calls=[call]), LLMResponse(content="请切换工作模式")])
    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt_id="att_1"))
    result = next(p for t, p in platform.events if t == "agent.tool_result")
    assert not result["ok"] and "content" in result["error"]
    assert broker.leases == 0
