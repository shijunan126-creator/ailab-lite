"""Workspace visual input is bounded, deterministic, and scoped to the run sandbox."""

import io
import shutil

import pytest
from PIL import Image

from ailab_platform.broker.workspace_view import view
from ailab_platform.sandbox import ExecResult
from ailab_platform.sandbox.transfer import TransferLimitError
from tests.test_workspace_previews import _manager


def test_workspace_view_supplies_private_listener(tmp_path, monkeypatch):
    manager, _, _ = _manager(tmp_path)
    lease = manager.create("profile-run")["lease_id"]
    commands = []
    original = manager.exec

    def capture(lease_id, command, timeout):
        commands.append(command)
        return original(lease_id, command, timeout)

    monkeypatch.setattr(manager, "exec", capture)
    data = io.BytesIO()
    Image.new("RGB", (5, 5), "white").save(data, "PNG")
    manager.put_file(lease, "/workspace/outputs/image.png", data.getvalue())
    view(manager, lease, "/workspace/outputs/image.png", None)
    view(manager, lease, "/workspace/outputs/image.png", None)
    invocations = [c for c in commands if "view_workspace_file.py" in c]
    assert len(invocations) == 2
    assert all("AILAB_LO_CLIENT=" in c and "AILAB_LO_RSS_LIMIT=" in c for c in invocations)


def image_bytes():
    data = io.BytesIO()
    Image.new("RGB", (80, 60), "red").save(data, "PNG")
    return data.getvalue()


def test_workspace_image_is_deterministic_and_rejects_escape(tmp_path):
    manager, _, _ = _manager(tmp_path)
    lease = manager.create("view-test")["lease_id"]
    manager.put_file(lease, "/workspace/outputs/image.png", image_bytes())
    first = view(manager, lease, "/workspace/outputs/image.png", None)
    assert first == view(manager, lease, "/workspace/outputs/image.png", [1])
    assert first["pages"] == [1] and first["images"]
    for path in ("/etc/passwd", "/workspace/../etc/passwd", "/workspace/.ailab/private.png"):
        with pytest.raises(ValueError, match="invalid_workspace_path"):
            view(manager, lease, path, None)
    with pytest.raises(ValueError, match="invalid_page_selection"):
        view(manager, lease, "/workspace/outputs/image.png", [1, 2, 3, 4, 5])
    manager.exec(lease, "ln -s /etc/passwd /workspace/outputs/link.jpg", 5)
    with pytest.raises(ValueError, match="view_failed"):
        view(manager, lease, "/workspace/outputs/link.jpg", None)
    manager.close()


def test_workspace_view_timeout_and_oversized_output(tmp_path, monkeypatch):
    manager, _, _ = _manager(tmp_path)
    lease = manager.create("view-limits")["lease_id"]
    manager.put_file(lease, "/workspace/outputs/image.png", image_bytes())
    execute = manager.exec
    monkeypatch.setattr(
        manager, "exec", lambda lid, cmd, timeout: ExecResult(124, "", "", 0) if "timeout -k" in cmd else execute(lid, cmd, timeout)
    )
    with pytest.raises(ValueError, match="view_timeout"):
        view(manager, lease, "/workspace/outputs/image.png", None)
    monkeypatch.setattr(manager, "exec", execute)

    def too_large(*args):
        raise TransferLimitError("too large")

    monkeypatch.setattr(manager, "_download", too_large)
    with pytest.raises(TransferLimitError):
        view(manager, lease, "/workspace/outputs/image.png", None)
    manager.close()


@pytest.mark.skipif(not all(shutil.which(x) for x in ["pdftoppm", "pdfinfo", "timeout"]), reason="PDF tools unavailable")
def test_workspace_pdf_renders_requested_pages_and_rejects_large_input(tmp_path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=120, height=72)
    data = io.BytesIO()
    writer.write(data)
    manager, _, _ = _manager(tmp_path)
    lease = manager.create("view-pages")["lease_id"]
    manager.put_file(lease, "/workspace/outputs/two.pdf", data.getvalue())
    result = view(manager, lease, "/workspace/outputs/two.pdf", [2, 1])
    assert result["pages"] == [2, 1] and len(result["images"]) == 2
    manager.exec(lease, "truncate -s 22000000 /workspace/outputs/large.pdf", 5)
    with pytest.raises(ValueError, match="view_failed"):
        view(manager, lease, "/workspace/outputs/large.pdf", None)
    manager.close()


def test_view_tool_appends_immutable_image_to_subsequent_requests(monkeypatch):
    import asyncio
    import base64
    import copy

    from ailab_platform.agent.loop import AgentLoop
    from ailab_platform.catalog import ModelSpec, get_catalog
    from ailab_platform.llm import LLMResponse, ToolCall
    from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM

    monkeypatch.setitem(
        get_catalog().models,
        "m",
        ModelSpec(id="m", name="m", provider="mock", capabilities=["tools", "vision"], context_tokens=200000),
    )

    class Broker(FakeBroker):
        async def view_workspace_file(self, lease_id, path, pages):
            return {"pages": [1], "images": [base64.b64encode(b"\xff\xd8\xfftest\xff\xd9").decode()]}

    class RecordingLLM(ScriptedLLM):
        async def chat(self, messages, tools, model, user_id=None):
            self.seen_messages.append(copy.deepcopy(messages))
            return self.responses.pop(0)

    def call(ident):
        return LLMResponse(tool_calls=[ToolCall(ident, "view_workspace_file", {"path": "/workspace/outputs/a.png"})])

    model = RecordingLLM([call("one"), call("two"), LLMResponse(content="done")])
    platform = FakePlatform()
    asyncio.run(AgentLoop(platform=platform, broker=Broker(), llm=model).process("run_1", attempt_id="attempt"))
    first = next(m for m in model.seen_messages[1] if isinstance(m.get("content"), list) and m["content"][-1].get("type") == "image_url")
    later = [m for m in model.seen_messages[2] if isinstance(m.get("content"), list) and m["content"][-1].get("type") == "image_url"]
    assert len(later) == 2 and later[0] == first
    assert first in platform.checkpoint["state"]["messages"]


def test_nonvision_model_rejects_view_before_allocating_sandbox(monkeypatch):
    import asyncio

    from ailab_platform.agent.loop import AgentLoop
    from ailab_platform.catalog import ModelSpec, get_catalog
    from ailab_platform.llm import LLMResponse, ToolCall
    from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM

    monkeypatch.setitem(get_catalog().models, "m", ModelSpec(id="m", name="m", provider="mock", capabilities=["tools"]))
    broker = FakeBroker()
    model = ScriptedLLM(
        [LLMResponse(tool_calls=[ToolCall("view", "view_workspace_file", {"path": "/workspace/a.png"})]), LLMResponse(content="done")]
    )
    asyncio.run(AgentLoop(platform=FakePlatform(), broker=broker, llm=model).process("run_1", attempt_id="attempt"))
    assert broker.leases == 0
    # The tool is not offered to a non-vision model; a hallucinated call is refused before any sandbox starts.
    assert any("not available" in m.get("content", "") for m in model.seen_messages[1] if m["role"] == "tool")


def test_nonvision_catalog_omits_visual_tools(monkeypatch):
    from ailab_platform.agent.loop import _tools_for_run
    from ailab_platform.catalog import ModelSpec, get_catalog

    monkeypatch.setitem(get_catalog().models, "m", ModelSpec(id="m", name="m", provider="mock", capabilities=["tools"]))
    names = {t["function"]["name"] for t in _tools_for_run({"model": "m", "mode": "work"})}
    assert "exec" in names and not names & {"view_attachment", "view_workspace_file"}


def test_warm_starts_the_listener_only_where_one_can_run(tmp_path, monkeypatch):
    from ailab_platform.broker.workspace_view import warm

    manager, _, _ = _manager(tmp_path)
    lease = manager.create("warm-run")["lease_id"]
    commands = []
    monkeypatch.setattr(manager, "exec", lambda lease_id, command, timeout: commands.append(command) or ExecResult(0, "", "", 1))
    # The mock shares the host and starts LibreOffice per call: nothing to warm.
    assert warm(manager, lease) == {"ok": False, "skipped": True} and commands == []
    monkeypatch.setattr(manager.provider, "office_listener", True)
    assert warm(manager, lease) == {"ok": True}
    assert "work_view_listener.py --ensure " in commands[0] and commands[-1].startswith("rm -rf -- /workspace/.ailab/view-warm-")


@pytest.mark.parametrize("prompt,warms", [("做一份演示文稿", 1), ("整理一下笔记", 0)])
def test_office_work_run_warms_the_view_listener_once_its_sandbox_exists(monkeypatch, prompt, warms):
    import asyncio

    from ailab_platform.agent.loop import AgentLoop
    from ailab_platform.catalog import ModelSpec, get_catalog
    from ailab_platform.llm import LLMResponse, ToolCall
    from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM

    monkeypatch.setitem(
        get_catalog().models, "m", ModelSpec(id="m", name="m", provider="mock", capabilities=["tools", "vision"], context_tokens=200000)
    )

    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "prompt": prompt, "options": {"mode": "work"}}

    class Broker(FakeBroker):
        def __init__(self):
            super().__init__()
            self.warmed = []

        async def warm_workspace_view(self, lease_id):
            self.warmed.append(lease_id)
            return {"ok": True}

    async def attested(broker, lease_id):
        return None

    monkeypatch.setattr("ailab_platform.agent.loop.verify_sandbox_skills", attested)
    broker = Broker()
    work = [ToolCall(f"c{i}", "exec", {"cmd": "true"}) for i in range(3)]
    model = ScriptedLLM([LLMResponse(tool_calls=[call]) for call in work] + [LLMResponse(content="done")])
    asyncio.run(AgentLoop(platform=Platform(), broker=broker, llm=model).process("run_1", attempt_id="attempt"))
    assert len(broker.warmed) == warms  # repeated sandbox work within a minute does not warm again
