"""The Agent may die after any sandbox step: a resumed attempt reuses finished work, never replays an uncertain exec,
and keeps one delivery identity per file. Also: lease loss, waiting for an answer, and answers that cannot leak."""

from __future__ import annotations

import asyncio
import json

import pytest

from ailab_platform.adapters.runtime import UNCERTAIN_EXEC, fresh_state
from ailab_platform.agent.loop import AgentLoop
from ailab_platform.clients import ClientError
from ailab_platform.config import settings
from ailab_platform.llm import LLMResponse, ToolCall
from ailab_platform.runtime_context import delivery_id_var
from tests.runtime_fakes import background_request
from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM

TERMINAL = {"succeeded", "failed", "canceled"}
WRITE = ToolCall("c_write", "write_file", {"path": "/workspace/outputs/a.md", "content": "a"})
EXEC = ToolCall("c_exec", "exec", {"cmd": "build"})
DELIVER = ToolCall("c_deliver", "deliver_file", {"path": "/workspace/outputs/a.md", "filename": "a.md"})


def script() -> list[LLMResponse]:
    return [
        LLMResponse(content="writing", tool_calls=[WRITE]),
        LLMResponse(content="building", tool_calls=[EXEC]),
        LLMResponse(content="delivering", tool_calls=[DELIVER]),
        LLMResponse(content="done"),
    ]


class Crash(BaseException):
    """Stands in for SIGKILL: not an Exception, so no handler in the loop may swallow it."""


def test_resume_rechecks_budget_before_pending_tools(monkeypatch):
    """A death after checkpointing the response must not bypass its budget gate."""
    monkeypatch.setattr(settings, "run_max_tokens", 100)
    platform, broker, llm = FakePlatform(), SandboxSpy(), ScriptedLLM([])
    platform.prior_usage = {"prompt_tokens": 80, "completion_tokens": 20}
    state = fresh_state()
    state["messages"] = [{"role": "user", "content": "build"}]
    state["pending"] = [{"id": EXEC.id, "name": EXEC.name, "arguments": EXEC.arguments, "ordinal": 0}]
    platform.checkpoint = {"step": 1, "state": state}

    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt=2, attempt_id="att_2"))

    assert broker.execs == []
    assert broker.leases == 0
    assert llm.seen_messages == []
    assert platform.statuses[-1][0] == "failed"
    assert "累计用量已达上限" in platform.statuses[-1][1]["error"]


class SandboxSpy(FakeBroker):
    def __init__(self, crash_at=None, when="before") -> None:
        super().__init__()
        self.crash_at, self.when = crash_at, when
        self.files: dict[str, bytes] = {}
        self.execs: list[str] = []
        self.commits: list[str | None] = []  # the delivery identity each commit arrived with
        self.steps = 0

    def _step(self, op, action):
        """Run one sandbox step; crash before or after it, as the test chose."""
        index = self.steps
        self.steps += 1
        crash = index == self.crash_at
        if crash and self.when == "before":
            raise Crash(op)
        result = action()
        if crash and self.when == "after":
            raise Crash(op)
        return result

    async def put_file(self, lease_id, path, data):
        def write():
            self.files[path] = data

        if path.startswith("/workspace/.ailab-"):
            write()
        else:
            self._step("write", write)

    async def exec(self, lease_id, cmd, timeout=None):
        if background_request(self, cmd)["action"] == "kill_all":
            return {"exit_code": 0, "stdout": json.dumps({"ok": True, "running": False, "killed": 0})}

        def run():
            self.execs.append(background_request(self, cmd)["cmd"])
            return {"exit_code": 0, "stdout": json.dumps({"exit_code": 0, "stdout": "built", "stderr": "", "duration_ms": 1})}

        return self._step("exec", run)

    async def commit(self, lease_id, path, filename=None, role="output"):
        def deliver():
            self.commits.append(delivery_id_var.get())
            return {"filename": filename or path.rsplit("/", 1)[-1], "size": len(self.files.get(path, b"")), "file_id": "f1"}

        return self._step("deliver", deliver)


# Steps are the sandbox operations in order: 0 write, 1 exec, 2 deliver. An exec that never started is run; one that may
# have started is reported as uncertain instead of run again.
EXECS_AFTER_RESUME = {
    (0, "before"): ["build"],
    (0, "after"): ["build"],
    (1, "before"): [],
    (1, "after"): ["build"],
    (2, "before"): ["build"],
    (2, "after"): ["build"],
}


@pytest.mark.parametrize("crash_at, when", list(EXECS_AFTER_RESUME), ids=lambda v: str(v))
def test_a_killed_agent_resumes_without_repeating_or_losing_work(crash_at, when):
    platform, llm = FakePlatform(), ScriptedLLM(script())
    broker = SandboxSpy(crash_at=crash_at, when=when)
    with pytest.raises(Crash):
        asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt_id="att_1"))
    assert platform.checkpoint is not None  # the boundary before the crash is durable

    broker.crash_at = None  # the replacement Agent's sandbox is healthy
    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt=2, attempt_id="att_2"))

    assert platform.statuses[-1][0] == "succeeded"
    assert {p: data for p, data in broker.files.items() if not p.startswith("/workspace/.ailab-")} == {
        "/workspace/outputs/a.md": b"a"
    }  # writes are idempotent overwrites
    state = platform.checkpoint["state"]
    assert {call_id: rec["name"] for call_id, rec in state["tool_log"].items()} == {
        "0:0": "write_file",
        "1:0": "exec",
        "2:0": "deliver_file",
    }
    assert broker.execs == EXECS_AFTER_RESUME[(crash_at, when)]
    # A delivery keeps the identity it had the first time: the API returns the recorded artifact for a repeat.
    assert len(set(broker.commits)) == 1 and broker.commits[-1] == "run_1:2:0"
    assert len(platform.checkpoint["state"]["artifacts"]) == 1


def test_an_exec_that_may_have_run_is_reported_to_the_model_not_retried():
    platform, llm = FakePlatform(), ScriptedLLM(script())
    broker = SandboxSpy(crash_at=1, when="after")  # the command ran, then the Agent died before recording its result
    with pytest.raises(Crash):
        asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt_id="att_1"))
    broker.crash_at = None
    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt=2, attempt_id="att_2"))

    assert broker.execs == ["build"]  # not replayed
    assert platform.checkpoint["state"]["tool_log"]["1:0"]["result"]["error"] == UNCERTAIN_EXEC
    assert any(
        m.get("role") == "tool" and m.get("tool_call_id") == "c_exec" and UNCERTAIN_EXEC in m["content"] for m in llm.seen_messages[-1]
    )


def test_a_lost_lease_interrupts_the_run_and_hands_it_to_the_next_attempt(monkeypatch):
    monkeypatch.setattr(settings, "runtime_heartbeat_seconds", 0.01)
    monkeypatch.setattr("ailab_platform.agent.loop.CANCEL_POLL_SECONDS", 0.01)

    class Lost(FakeBroker):
        async def renew(self, lease_id):
            raise ClientError("POST", "/renew", 410, '{"detail":"lease_lost"}')

        async def exec(self, lease_id, cmd, timeout=None):
            await asyncio.sleep(5)  # a long command: the heartbeat notices the loss while it runs
            return {"exit_code": 0, "stdout": json.dumps({"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1})}

    platform = FakePlatform()
    llm = ScriptedLLM([LLMResponse(content="run", tool_calls=[EXEC]), LLMResponse(content="done")])
    broker = Lost()
    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt_id="att_1"))

    assert platform.paused["status"] == "interrupted" and platform.paused["auto_resume"] is True
    assert not any(status in TERMINAL for status, _ in platform.statuses)  # interrupted is not failed


def test_transient_renewal_failures_do_not_cost_the_lease(monkeypatch):
    monkeypatch.setattr(settings, "runtime_heartbeat_seconds", 0.01)
    failures = {"left": 2}

    class Flaky(FakeBroker):
        async def renew(self, lease_id):
            if failures["left"]:
                failures["left"] -= 1
                raise ClientError("POST", "/renew", 503, "busy")

        async def exec(self, lease_id, cmd, timeout=None):
            for _ in range(200):  # long enough for the heartbeat to fire a few times
                await asyncio.sleep(0.005)
                if failures["left"] == 0:
                    break
            return {"exit_code": 0, "stdout": json.dumps({"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1})}

    platform = FakePlatform()
    llm = ScriptedLLM([LLMResponse(content="run", tool_calls=[EXEC]), LLMResponse(content="done")])
    asyncio.run(AgentLoop(platform=platform, broker=Flaky(), llm=llm).process("run_1", attempt_id="att_1"))
    assert failures["left"] == 0 and platform.statuses[-1][0] == "succeeded" and platform.paused is None


def test_asking_releases_the_sandbox_before_the_run_waits():
    events = []

    class Recording(FakeBroker):
        async def release(self, lease_id):
            events.append("sandbox released")

    class Pausing(FakePlatform):
        async def pause_run(self, run_id, payload):
            events.append(f"paused {payload['status']}")
            return await super().pause_run(run_id, payload)

    ask = ToolCall("c_ask", "ask_user", {"question": "哪个？", "options": ["A", "B"], "allow_text": False})
    platform = Pausing()
    llm = ScriptedLLM([LLMResponse(content="asking", tool_calls=[EXEC]), LLMResponse(content="asking", tool_calls=[ask])])
    asyncio.run(AgentLoop(platform=platform, broker=Recording(), llm=llm).process("run_1", attempt_id="att_1"))

    assert events[-2:] == ["sandbox released", "paused waiting_user"]
    assert platform.paused["question"]["question_id"] == "run_1:1:0"
    assert not any(status in TERMINAL for status, _ in platform.statuses)  # waiting is not a result


def test_an_answer_resumes_the_question_it_was_given_for_and_only_that_one():
    ask_first = ToolCall("c_ask1", "ask_user", {"question": "哪个？"})
    ask_second = ToolCall("c_ask1", "ask_user", {"question": "再确认？"})
    platform = FakePlatform()
    llm = ScriptedLLM(
        [
            LLMResponse(content="asking", tool_calls=[ask_first]),
            LLMResponse(content="asking again", tool_calls=[ask_second]),
            LLMResponse(content="done"),
        ]
    )

    def run(attempt):
        asyncio.run(
            AgentLoop(platform=platform, broker=FakeBroker(), llm=llm).process("run_1", attempt=attempt, attempt_id=f"att_{attempt}")
        )

    run(1)
    assert platform.paused["question"]["question_id"] == "run_1:0:0"
    platform.checkpoint = {**platform.checkpoint, "answer": {"question_id": "run_1:0:0", "answer": "A"}}
    run(2)
    # The first question was answered; the second was never answered, so the run must pause again, not reuse "A".
    assert platform.paused["question"]["question_id"] == "run_1:1:0"
    assert platform.checkpoint["state"]["tool_log"]["0:0"]["result"] == {"ok": True, "compacted": True}
    assert any('"answer": "A"' in str(m.get("content")) for m in platform.checkpoint["state"]["messages"])
    assert "answer" not in platform.checkpoint["state"]


def test_a_missing_workspace_is_reported_on_resume_instead_of_assumed_present():
    class NoSnapshot(SandboxSpy):
        async def snapshot(self, lease_id):
            raise RuntimeError("snapshot unavailable")

    platform = FakePlatform()
    llm = ScriptedLLM(script())
    broker = NoSnapshot(crash_at=2, when="before")  # write and exec happened; no snapshot was ever taken
    with pytest.raises(Crash):
        asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt_id="att_1"))
    assert platform.checkpoint["state"]["workspace"] is None

    broker.crash_at = None
    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt=2, attempt_id="att_2"))
    notes = [m["content"] for m in llm.seen_messages[-1] if m.get("role") == "system"]
    assert any("没有保存下来" in note for note in notes)


def test_reused_provider_call_id_does_not_deduplicate_a_later_tool_invocation():
    platform, broker = FakePlatform(), SandboxSpy()
    llm = ScriptedLLM(
        [
            LLMResponse(tool_calls=[ToolCall("reused", "exec", {"cmd": "first"})]),
            LLMResponse(tool_calls=[ToolCall("reused", "exec", {"cmd": "second"})]),
            LLMResponse(content="done"),
        ]
    )
    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=llm).process("run_1", attempt_id="attempt"))
    assert broker.execs == ["first", "second"]


def test_an_unchanged_snapshot_is_not_reported_as_lagging_after_a_model_crash():
    from ailab_platform.agent.loop import WORKSPACE_LAG_NOTE

    class DiesBeforeReply(ScriptedLLM):
        async def chat(self, *args, **kwargs):
            if not self.responses:
                raise Crash("before model reply")
            return await super().chat(*args, **kwargs)

    platform, broker = FakePlatform(), SandboxSpy()
    first = DiesBeforeReply([LLMResponse(tool_calls=[WRITE])])
    with pytest.raises(Crash):
        asyncio.run(AgentLoop(platform=platform, broker=broker, llm=first).process("run_1", attempt_id="first"))
    resumed = ScriptedLLM([LLMResponse(content="done")])
    asyncio.run(AgentLoop(platform=platform, broker=broker, llm=resumed).process("run_1", attempt=2, attempt_id="next"))
    assert not any(message.get("content") == WORKSPACE_LAG_NOTE for message in resumed.seen_messages[0])
