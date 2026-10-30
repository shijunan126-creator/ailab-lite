"""The office skill catalog and multi-skill message contract."""

import asyncio

from fastapi.testclient import TestClient

from ailab_platform.agent.loop import AgentLoop
from ailab_platform.agent.prompts import load_skill, render_skill_mentions, system_prompt_for
from ailab_platform.api.main import app
from ailab_platform.llm import LLMResponse

from .test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM

AUTH = {"Authorization": "Bearer test-token"}


def test_base_catalog_and_multi_skill_message():
    with TestClient(app) as client:
        skills = client.get("/v1/skills", headers=AUTH).json()
        assert [s["name"] for s in skills[:4]] == ["platform.pdf", "platform.docx", "platform.xlsx", "platform.pptx"]
        assert all(s["kind"] == "base" and s["enabled"] and not s["toggleable"] for s in skills[:4])
        assert client.put("/v1/skills/platform.pdf", headers=AUTH, json={"enabled": False}).status_code == 403
        response = client.post(
            "/v1/runs",
            headers=AUTH,
            json={"prompt": "甲😀乙", "mode": "chat", "skills": [{"name": "xlsx", "at": 2}, {"name": "pptx", "at": 0}]},
        )
        assert response.status_code == 201, response.text
        options = response.json()["options"]
        assert options["mode"] == "chat"
        assert [s["name"] for s in options["skills"]] == ["platform.xlsx", "pptx"]
        assert render_skill_mentions("甲😀乙", options["skills"]) == "[技能：PPT 设计]甲😀[技能：Excel]乙"
        assert client.post("/v1/runs", headers=AUTH, json={"prompt": "x", "skills": [{"name": "pptx", "at": 2}]}).status_code == 400
        assert client.post("/v1/runs", headers=AUTH, json={"prompt": "x", "skill": "pptx", "skills": []}).status_code == 400


def test_chat_base_text_and_prompt_are_mode_safe():
    text = load_skill("platform.pdf", mode="chat")
    assert "read_library_file" in text and "next_cursor" in text
    assert "pdf_tool.py" not in text and "当前对话不能运行脚本" in text
    assert "pdf_tool.py" in load_skill("platform.pdf", mode="work")
    assert "pdf_tool.py" not in system_prompt_for("chat", "platform.pdf")


def test_picked_and_attached_skills_are_loaded_before_model_without_sandbox():
    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {
                **await super().get_run(run_id),
                "options": {"mode": "chat", "skills": [{"name": "pptx", "title": "PPT 设计", "at": 0}]},
                "input_files": [{"filename": "report.pdf", "content_type": "application/pdf"}],
            }

    platform, broker = Platform(), FakeBroker()
    llm = ScriptedLLM([LLMResponse(content="done")])
    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt_id="att_1"))
    events = [p for t, p in platform.events if t == "agent.skill"]
    assert [(e["name"], e["status"], e["reason"]) for e in events] == [("pptx", "loaded", "picked"), ("platform.pdf", "loaded", "office")]
    messages = llm.seen_messages[0]
    assert [m["role"] for m in messages][-5:] == ["user", "assistant", "tool", "assistant", "tool"]
    assert "[技能：PPT 设计]" in str(messages[-5]["content"])
    assert broker.leases == 0


def test_resume_reinstalls_personal_files_when_lease_is_acquired():
    from ailab_platform.agent.loop import _Turn

    from .test_personal_skills import SKILL, ZIP

    class Platform(FakePlatform):
        async def get_file_bytes(self, file_id, run_id=None):
            assert file_id == "f1"
            return ZIP

    async def exercise():
        broker = FakeBroker()
        loop = AgentLoop(platform=Platform(), broker=broker)
        turn = _Turn({"options": {"mode": "work"}})
        turn.personal = {"weekly": SKILL}
        loop._states["run_1"] = {"loaded_skills": [{"name": "weekly", "kind": "personal", "file_id": "f1"}]}
        await loop._ensure_lease(turn, "run_1")
        await loop._ensure_lease(turn, "run_1")
        paths = [p for p, _ in broker.put_calls]
        assert paths.count("/workspace/.ailab/skills/weekly/scripts/fill.py") == 1
        assert broker.leases == 1

    asyncio.run(exercise())


def test_platform_skill_refuses_unverified_lease():
    import httpx
    import pytest

    from ailab_platform.agent.loop import _Turn
    from ailab_platform.agent.skill_verification import SkillRefused

    class Broker(FakeBroker):
        def _req(self, *args, **kwargs):
            return kwargs

        async def _send(self, req):
            return httpx.Response(200, json={"verified": False})

    async def exercise():
        broker = Broker()
        loop = AgentLoop(platform=FakePlatform(), broker=broker)
        loop._states["run_1"] = {"loaded_skills": [{"name": "platform.pdf", "kind": "base"}]}
        turn = _Turn({"options": {"mode": "work"}})
        with pytest.raises(SkillRefused):
            await loop._ensure_lease(turn, "run_1")
        with pytest.raises(SkillRefused):
            await loop._ensure_lease(turn, "run_1")
        assert broker.leases == 1

    asyncio.run(exercise())


def test_resume_loads_office_detected_before_checkpoint():
    from ailab_platform.adapters.runtime import fresh_state

    state = fresh_state()
    state.update(
        messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "inspect"}],
        pending_office_skills=["platform.pdf"],
        skill_initialization_done=True,
    )
    platform = FakePlatform()
    platform.checkpoint = {"step": 1, "state": state}
    llm = ScriptedLLM([LLMResponse(content="done")])
    asyncio.run(AgentLoop(platform=platform, broker=FakeBroker(), llm=llm).process("run_1", attempt_id="att_1"))
    assert any(m["role"] == "tool" and "next_cursor" in m["content"] for m in llm.seen_messages[0])
    assert any(t == "agent.skill" and p["name"] == "platform.pdf" and p["reason"] == "office" for t, p in platform.events)
