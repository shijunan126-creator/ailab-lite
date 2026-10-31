"""Stream compatibility, early title concurrency and threshold-driven compaction."""

import asyncio

import pytest

from ailab_platform.agent.loop import AgentLoop, web_sources
from ailab_platform.agent.tools import tools_for_chat, tools_for_turn
from ailab_platform.catalog import Catalog, ModelSpec
from ailab_platform.llm import LLMClient, LLMResponse, _StreamState
from tests.test_agent_loop import FakeBroker, FakePlatform


@pytest.mark.parametrize(
    "delta",
    [
        {"reasoning_content": "思考"},
        {"reasoning": "思考"},
        {"thinking": "思考"},
        {"reasoning_details": [{"text": "思考"}]},
        {"reasoning_details": [{"summary": "思考"}]},
    ],
)
def test_all_reasoning_stream_shapes(delta):
    state = _StreamState()
    assert state.feed({"choices": [{"delta": delta}]}) == ("", "思考")
    assert state.response().reasoning_content == "思考"


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_last_reasoning_chunk_flushes_with_no_answer_chunk(mode):
    class Stream(LLMClient):
        async def chat_stream(self, *args, on_reasoning=None, **kwargs):
            await on_reasoning("最后一块")
            return LLMResponse(reasoning_content="最后一块")

    async def run():
        platform = FakePlatform()
        await AgentLoop(platform=platform)._model_attempt(
            Stream(), "r", {"_attempt_id": "a", "options": {"mode": mode}}, "m", [], 0, tools=[]
        )
        events = [p for t, p in platform.events if t == "agent.thinking"]
        assert len(events) == 1 and events[0]["text"] == "最后一块" and "seconds" in events[0]

    asyncio.run(run())


@pytest.mark.parametrize("slow_title", [False, True])
def test_title_is_concurrent_and_slow_title_never_delays_answer(slow_title, monkeypatch):
    monkeypatch.setattr("ailab_platform.agent.loop.TITLE_TIMEOUT_SECONDS", 0.1)

    async def run():
        main_started, titled = asyncio.Event(), asyncio.Event()
        title_closed = asyncio.Event()

        class Platform(FakePlatform):
            async def get_run(self, id_):
                return {**await super().get_run(id_), "needs_conversation_title": True, "options": {"mode": "chat"}}

            async def set_conversation_title(self, id_, title):
                assert main_started.is_set() and title == ("hi" if slow_title else "概括问题")
                titled.set()
                await self.append_event(id_, "conversation.titled", {"conversation_id": "c", "title": title})

        class Model(LLMClient):
            async def chat(self, messages, tools, model, user_id=None):
                if "会话标题" in messages[0]["content"]:
                    try:
                        await main_started.wait()
                        assert "answer" not in messages[1]["content"]
                        if slow_title:
                            await asyncio.Event().wait()
                        return LLMResponse(content="概括问题")
                    finally:
                        title_closed.set()
                main_started.set()
                if not slow_title:
                    await asyncio.wait_for(titled.wait(), 1)
                else:
                    await asyncio.sleep(0.01)
                return LLMResponse(content="answer")

        p = Platform()
        await asyncio.wait_for(AgentLoop(platform=p, broker=FakeBroker(), llm=Model()).process("r", attempt_id="a"), 2)
        assert p.statuses[-1][0] == "succeeded" and title_closed.is_set()
        assert titled.is_set()
        assert len(p.reservations) == 2 and len(p.settlements) == 2
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]

    asyncio.run(run())


def test_citation_sources_are_bounded_valid_and_deduplicated():
    sources = web_sources(
        {
            "results": [
                {"title": "标题" * 300, "url": "https://example.com/a"},
                {"url": "https://example.com/a"},
                {"url": "file:///etc/passwd"},
                *[{"url": f"https://example.com/{i}"} for i in range(20)],
            ]
        }
    )
    assert len(sources) == 10 and len(sources[0]["title"]) == 200 and sources[0]["site"] == "example.com"
    assert web_sources({"url": "https://example.com/b", "title": "page"}) == [
        {"url": "https://example.com/b", "title": "page", "site": "example.com"}
    ]


def test_library_tool_modes():
    chat = {t["function"]["name"] for t in tools_for_chat()}
    work = {t["function"]["name"] for t in tools_for_turn()}
    assert {"search_library", "read_library_file"} <= chat
    assert "copy_library_file" in work and "copy_library_file" not in chat


@pytest.mark.parametrize("enabled", [False, True])
def test_context_threshold_triggers_before_overflow(monkeypatch, enabled):
    cat = Catalog([ModelSpec("m", "m", provider="mock", context_tokens=10000, max_output_tokens=1000)])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: cat)
    monkeypatch.setattr("ailab_platform.agent.loop.estimator.estimate", lambda model, value: len(str(value)))
    monkeypatch.setattr("ailab_platform.agent.loop.tools_for_turn", lambda: [])

    class Context(FakePlatform):
        saved = []

        async def get_model_context(self, *args, **kwargs):
            return {
                "summary": "",
                "through_run_id": None,
                "history": [{"id": "old", "prompt": "a" * 5500, "reply": "b"}],
                "has_more": False,
            }

        async def save_model_summary(self, id_, payload):
            self.saved.append(payload)

    class Summary(LLMClient):
        async def chat(self, *args, **kwargs):
            return LLMResponse(content="摘要")

    async def run():
        p = Context()
        p.saved = []
        base = [{"role": "system", "content": "system"}, {"role": "user", "content": "question"}]
        await AgentLoop(platform=p, llm=Summary())._assemble_context(
            "r", {"_attempt_id": "a", "context_settings": {"auto_compact": enabled, "threshold_ratio": 0.5}}, "m", base
        )
        assert bool(p.saved) == enabled

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_real_agent_api_path_persists_reasoning_and_early_title(mode):
    import httpx
    from fastapi.testclient import TestClient

    from ailab_platform.api.main import app
    from ailab_platform.clients import AsyncPlatformClient
    from tests.run_claims import attempt_headers
    from tests.test_conversations import AUTH

    async def scenario(c, run):
        title_done = asyncio.Event()

        class Model(LLMClient):
            async def chat_stream(self, messages, tools, model, on_text=None, on_reasoning=None, **kwargs):
                if "会话标题" in messages[0]["content"]:
                    return LLMResponse(content="即时问题标题", usage={"prompt_tokens": 1, "completion_tokens": 1})
                # Wait on API-observed title persistence, while no answer exists yet.
                for _ in range(100):
                    history = c.get(f"/v1/runs/{run['id']}/events/history", headers=AUTH).json()
                    if any(e["type"] == "conversation.titled" for e in history):
                        title_done.set()
                        break
                    await asyncio.sleep(0.01)
                assert title_done.is_set()
                await on_reasoning("公开思考最后一块")
                await on_text("确认回答")
                return LLMResponse(
                    content="确认回答", reasoning_content="公开思考最后一块", usage={"prompt_tokens": 1, "completion_tokens": 1}
                )

        platform = AsyncPlatformClient("http://testserver", token="test-service")
        await platform._http.aclose()
        platform._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        try:
            owner = attempt_headers(c, run["id"])
            await AgentLoop(platform=platform, broker=FakeBroker(), llm=Model()).process(run["id"], attempt_id=owner["X-Run-Attempt"])
            assert c.get(f"/v1/runs/{run['id']}", headers=AUTH).json()["status"] == "succeeded"
            history = c.get(f"/v1/runs/{run['id']}/events/history", headers=AUTH).json()
            thinking = [e["payload"]["text"] for e in history if e["type"] == "agent.thinking"]
            assert "".join(thinking) == "公开思考最后一块"
            assert next(i for i, e in enumerate(history) if e["type"] == "conversation.titled") < next(
                i for i, e in enumerate(history) if e["type"] == "agent.delta"
            )
        finally:
            await platform.aclose()

    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "问题", "mode": mode}).json()
        asyncio.run(scenario(c, run))


def test_optional_title_cannot_take_quota_before_main_reservation():
    async def run():
        class Platform(FakePlatform):
            first = True

            async def get_run(self, id_):
                return {**await super().get_run(id_), "needs_conversation_title": True, "options": {"mode": "chat"}}

            async def reserve_usage(self, payload):
                # Force a scheduling gap while the main reservation is in flight.
                if self.first:
                    self.first = False
                    assert payload["max_output_tokens"] != 2048
                    await asyncio.sleep(0.02)
                    assert not self.reservations
                    return await super().reserve_usage(payload)
                assert payload["max_output_tokens"] == 2048
                raise RuntimeError("remaining quota insufficient for optional metadata")

        class Model(LLMClient):
            async def chat(self, *args, **kwargs):
                await asyncio.sleep(0.03)
                return LLMResponse(content="answer")

        p = Platform()
        await AgentLoop(platform=p, broker=FakeBroker(), llm=Model()).process("r", attempt_id="a")
        assert p.statuses[-1][0] == "succeeded" and len(p.reservations) == len(p.settlements) == 1

    asyncio.run(run())


@pytest.mark.parametrize("stop", [None, "shutdown", "attempt_loss"])
def test_title_survives_answer_completion_and_is_owned_until_finished(stop, monkeypatch):
    from ailab_platform.clients import ClientError

    monkeypatch.setattr("ailab_platform.agent.loop.CANCEL_POLL_SECONDS", 0.01)

    async def scenario():
        completed, release_title, closed, titled = (asyncio.Event() for _ in range(4))

        class Platform(FakePlatform):
            async def get_run(self, id_):
                return {**await super().get_run(id_), "needs_conversation_title": True, "options": {"mode": "chat"}}

            async def get_run_state(self, id_):
                if stop == "attempt_loss" and completed.is_set():
                    raise ClientError("GET", "/state", 409, "stale_attempt")
                return await super().get_run_state(id_)

            async def set_run_status(self, *args, **kwargs):
                await super().set_run_status(*args, **kwargs)
                if self.statuses[-1][0] == "succeeded":
                    completed.set()

            async def set_conversation_title(self, id_, title):
                assert completed.is_set() and title == "后到标题"
                titled.set()

        class Model(LLMClient):
            async def chat(self, messages, *args, **kwargs):
                if "会话标题" in messages[0]["content"]:
                    try:
                        await release_title.wait()
                        return LLMResponse(content="后到标题")
                    finally:
                        closed.set()
                await asyncio.sleep(0.02)
                return LLMResponse(content="answer")

        platform = Platform()
        task = asyncio.create_task(AgentLoop(platform=platform, broker=FakeBroker(), llm=Model()).process("r", attempt_id="a"))
        await asyncio.wait_for(completed.wait(), 1)
        assert not task.done() and not titled.is_set()
        if stop == "shutdown":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif stop == "attempt_loss":
            await asyncio.wait_for(task, 1)
        else:
            release_title.set()
            await asyncio.wait_for(task, 1)
        assert titled.is_set() == (stop is None)
        assert closed.is_set() and platform.statuses[-1][0] == "succeeded"
        assert len(platform.reservations) == len(platform.settlements) == 2
        assert len(asyncio.all_tasks()) == 1

    asyncio.run(scenario())


def test_slow_gate_does_not_consume_title_model_budget(monkeypatch):
    monkeypatch.setattr("ailab_platform.agent.loop.TITLE_TIMEOUT_SECONDS", 0.05)

    async def scenario():
        class Platform(FakePlatform):
            async def set_conversation_title(self, rid, title):
                self.title = title

        class Model(LLMClient):
            async def chat(self, *args, **kwargs):
                await asyncio.sleep(0.02)
                return LLMResponse(content="模型标题")

        platform = Platform()
        gate = asyncio.Event()
        run = {**await platform.get_run("r"), "needs_conversation_title": True, "_attempt_id": "a", "_main_model_reserved": gate}
        task = asyncio.create_task(AgentLoop(platform=platform, llm=Model())._conversation_title("r", run))
        await asyncio.sleep(0.08)
        assert not task.done() and not platform.reservations
        gate.set()
        assert await task == "模型标题" and platform.title == "模型标题"
        assert len(platform.reservations) == len(platform.settlements) == 1

    asyncio.run(scenario())


def test_title_timeout_applies_normalized_prompt_fallback(monkeypatch):
    monkeypatch.setattr("ailab_platform.agent.loop.TITLE_TIMEOUT_SECONDS", 0.02)

    async def scenario():
        class Platform(FakePlatform):
            async def set_conversation_title(self, rid, title):
                self.title = title

        class Model(LLMClient):
            async def chat(self, *args, **kwargs):
                await asyncio.Event().wait()

        platform = Platform()
        run = {
            **await platform.get_run("r"),
            "_attempt_id": "a",
            "prompt": "\n# **资源释放** `指南`\n额外说明",
            "needs_conversation_title": True,
        }
        assert await AgentLoop(platform=platform, llm=Model())._conversation_title("r", run) == "资源释放 指南"
        assert platform.title == "资源释放 指南"
        assert len(platform.reservations) == len(platform.settlements) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("fallback", [False, True])
def test_real_late_title_reaches_open_sse_after_run_completed(monkeypatch, fallback):
    import threading

    import httpx
    from fastapi.testclient import TestClient

    from ailab_platform.api import routes_runs
    from ailab_platform.api.main import app
    from ailab_platform.clients import AsyncPlatformClient
    from tests.run_claims import attempt_headers
    from tests.test_conversations import AUTH

    waiting = threading.Event()
    original = routes_runs._poll_events

    def poll(*args):
        result = original(*args)
        if not result[0] and result[2]:
            waiting.set()
        return result

    monkeypatch.setattr(routes_runs, "_poll_events", poll)

    async def scenario(c, run):
        class Model(LLMClient):
            async def chat(self, messages, *args, **kwargs):
                if "会话标题" in messages[0]["content"]:
                    assert await asyncio.to_thread(waiting.wait, 2)
                    if fallback:
                        raise TimeoutError("metadata inference timed out")
                    return LLMResponse(content="# **晚到** `标题`", usage={"prompt_tokens": 1, "completion_tokens": 1})
                await asyncio.sleep(0.02)
                return LLMResponse(content="answer", usage={"prompt_tokens": 1, "completion_tokens": 1})

        platform = AsyncPlatformClient("http://testserver", token="test-service")
        await platform._http.aclose()
        platform._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        owner = attempt_headers(c, run["id"])
        try:
            _, response = await asyncio.wait_for(
                asyncio.gather(
                    AgentLoop(platform=platform, broker=FakeBroker(), llm=Model()).process(run["id"], attempt_id=owner["X-Run-Attempt"]),
                    asyncio.to_thread(c.get, f"/v1/runs/{run['id']}/events", headers=AUTH),
                ),
                4,
            )
            stream = response.text
            assert stream.index("event: run.completed") < stream.index("event: conversation.titled") < stream.index("event: stream.end")
            assert c.get(f"/v1/conversations/{run['conversation_id']}", headers=AUTH).json()["title"] == (
                "问题" if fallback else "晚到 标题"
            )
        finally:
            await platform.aclose()

    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "问题", "mode": "chat"}).json()
        asyncio.run(scenario(c, run))


def test_system_prefix_is_shared_and_memory_changes_append_notes():
    from ailab_platform.agent.prompts import memory_prompt
    from tests.test_agent_loop import ScriptedLLM

    class Platform(FakePlatform):
        def __init__(self, uid, entry, project):
            super().__init__()
            self.uid, self.project = uid, project
            self.current = {"revision": 1, "enabled": True, "auto_save": True, "entries": [entry]}

        async def get_run(self, run_id):
            return {
                **await super().get_run(run_id),
                "user_id": self.uid,
                "options": {"mode": "chat"},
                "project_instructions": self.project,
                "memory": self.current,
            }

        async def get_memory_context(self, run_id):
            return self.current

    async def exercise():
        first = {"id": "m1", "kind": "user", "content": "中文"}
        second = {"id": "m2", "kind": "user", "content": "简洁"}
        p1, p2 = Platform("u1", first, "项目一"), Platform("u2", second, "项目二")
        llm1, llm2 = ScriptedLLM([LLMResponse(content="ok")]), ScriptedLLM([LLMResponse(content="ok")])
        await AgentLoop(platform=p1, broker=FakeBroker(), llm=llm1).process("r1", attempt_id="a")
        await AgentLoop(platform=p2, broker=FakeBroker(), llm=llm2).process("r2", attempt_id="a")
        left, right = llm1.seen_messages[0], llm2.seen_messages[0]
        assert left[0] == right[0] and left[1] != right[1]
        assert "项目一" in left[1]["content"] and "项目二" in right[1]["content"]
        messages = [dict(m) for m in left]
        original = [dict(m) for m in messages[:2]]
        loop = AgentLoop(platform=p1)
        loop._states["r1"] = {"memory_observed": p1.current}
        p1.current = {**p1.current, "revision": 2, "entries": [first, second]}
        await loop._model_attempt(ScriptedLLM([LLMResponse(content="ok")]), "r1", {"_attempt_id": "a"}, "m", messages, 0, tools=[])
        assert messages[:2] == original
        p1.current = {**p1.current, "revision": 3, "entries": [second]}
        await loop._model_attempt(ScriptedLLM([LLMResponse(content="ok")]), "r1", {"_attempt_id": "a"}, "m", messages, 1, tools=[])
        assert messages[:2] == original
        assert "记忆 m2：简洁" in messages[-2]["content"]
        assert "记忆 m1 已删除，不得再使用" in messages[-1]["content"]
        assert "revision=" not in memory_prompt(p1.current)

    asyncio.run(exercise())
