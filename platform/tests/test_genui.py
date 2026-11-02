"""P3 contract and fence regressions; all model calls are deterministic mocks."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ailab_platform import metrics
from ailab_platform.agent.prompts import history_messages, system_prompt_for
from ailab_platform.api.main import app
from ailab_platform.api.routes_genui import telemetry_throttle
from ailab_platform.catalog import CatalogError, load_catalog
from ailab_platform.config import Settings, settings
from ailab_platform.contracts.genui import component_names, full_prompt, guide_for, index_for, load_contract
from ailab_platform.domain.answer_parts import for_history, inspect, split
from ailab_platform.llm import MockLLM
from tests.run_claims import SERVICE
from tests.test_conversations import AUTH, _finish

FIXTURES = Path(__file__).parent / "fixtures" / "genui"
UI = '```openui\nroot = Metric("次数", 3)\n```\n'


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.md")), ids=lambda p: p.stem)
def test_shared_split_fixtures(path):
    text = path.read_bytes().decode()
    expected = json.loads(path.with_suffix(".json").read_text())
    assert split(text) == expected


def test_split_half_nested_long_fences_and_inspection():
    assert split("text\n```op") == [{"kind": "md", "text": "text\n```op", "closed": True, "index": 0}]
    assert not inspect("~~~md\n" + UI + "~~~\n")["blocks"]
    assert not inspect("```md\n" + UI + "```\n")["blocks"]
    assert split("````openui\nx\n```\n`````\n")[0]["text"] == "x\n```\n"
    assert inspect('```openui\nroot = Strange(Text("Metric(fake)"))')["components"] == {"unknown": 1, "Text": 1}
    report = inspect("```openui\n" + "x" * 32001)
    assert (report["blocks"], report["unclosed"], report["oversize"]) == (1, 1, 1)


def test_history_boundaries_and_placeholders():
    text = "before\n" + UI + "after\n"
    assert for_history("普通回答" * 100, True, 12) == ("普通回答" * 100)[:12]
    assert for_history("普通回答" * 100, False, 12) == ("普通回答" * 100)[:12]
    assert for_history(UI + "普通回答" * 100, False, 14) == "[界面：Metric]普通回"
    assert for_history(text, True, 1000) == text
    assert for_history(text, False, 1000) == "before\n[界面：Metric]after\n"
    assert for_history(UI, True, 20) == "[界面：Metric]"
    assert for_history(UI, True, 1) == ""
    for limit in range(len(text) + 1):
        value = for_history(text, True, limit)
        assert len(value) <= limit
        if "```openui" in value:
            assert value.count("```") == 2
    value = for_history('```openui\nroot = Stack([Metric("a", 1), Metric("b", 2), Metric("c", 3)])\n```', False, 100)
    assert value == "[界面：Stack、Metric×3]"
    # Every turn renders the same in every later request, so an earlier reply is never rewritten (prefix cache).
    messages = history_messages([{"reply": UI}, {"reply": UI}])
    assert UI == messages[1]["content"] == messages[3]["content"]


@pytest.mark.parametrize(
    "key,value", [("genui_max_blocks", 0), ("genui_max_blocks", 21), ("genui_max_block_chars", 999), ("genui_max_block_chars", 64001)]
)
def test_config_ranges(key, value):
    with pytest.raises(ValidationError):
        Settings(**{key: value})


def test_contract_sha_and_secure_gate(tmp_path, monkeypatch):
    catalog, index, guide = load_contract()
    for kind, text in (("index", index), ("guide", guide)):
        assert hashlib.sha256(text.encode()).hexdigest() == catalog[f"{kind}_sha256"]
        (tmp_path / f"openui-v2.{kind}.md").write_text(text)
    (tmp_path / "catalog-v2.json").write_text(json.dumps(catalog))
    (tmp_path / "openui-v2.guide.md").write_text(guide + "tamper")
    with pytest.raises(ValueError, match="sha256"):
        load_contract(tmp_path)
    monkeypatch.setattr("ailab_platform.contracts.genui.load_contract", lambda: load_contract(tmp_path))
    with pytest.raises(RuntimeError, match="sha256"):
        Settings(profile="secure", genui_enabled=True).validate_secrets()


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_prompt_admission_stable_prefix(mode, monkeypatch):
    monkeypatch.setattr(settings, "genui_enabled", True)
    monkeypatch.setattr(settings, "genui_html_enabled", False)
    enabled = system_prompt_for(mode, genui=True, picked_text="PRIVATE SKILL", deferred=True)
    assert index_for(False) in enabled
    assert "PRIVATE SKILL" not in enabled
    assert "HtmlArtifact" not in index_for(False)
    assert index_for(False) not in system_prompt_for(mode, genui=False)
    monkeypatch.setattr(settings, "genui_enabled", False)
    assert index_for(False) not in system_prompt_for(mode, genui=True)


def test_catalog_capability(tmp_path):
    source = Path(__file__).with_name("models.test.toml").read_text()
    # The catalog is TOML and validates explicit capabilities on loading.
    source = source.replace('provider = "mock"', 'provider = "mock"\ncapabilities = ["tools", "genui"]', 1)
    path = tmp_path / "models.toml"
    path.write_text(source)
    assert any(spec.supports({"genui"}) for spec in load_catalog(path).models.values())
    path.write_text(source.replace('["tools", "genui"]', '["invented"]'))
    with pytest.raises(CatalogError, match="capabilities"):
        load_catalog(path)


def test_bounded_server_metrics():
    metrics.record_genui(UI + "```openui\nroot = Attacker(\n", "attacker", max_blocks=1)
    metrics.record_genui("```openui\n" + "x" * 32001, "chat")
    for family in metrics.GENUI_COMPONENTS.collect():
        for sample in family.samples:
            assert sample.labels["component"] in component_names() | {"unknown"}
    outcomes = {sample.labels["outcome"] for family in metrics.GENUI_BLOCKS.collect() for sample in family.samples}
    assert {"closed", "over_limit", "oversize"} <= outcomes
    assert all(sample.labels["mode"] in {"chat", "work"} for family in metrics.GENUI_ANSWERS.collect() for sample in family.samples)


def test_telemetry_auth_validation_rate_and_metrics():
    telemetry_throttle._failures.clear()
    with TestClient(app) as client:
        assert client.post("/v1/genui/telemetry", json=[]).status_code == 401
        for body in ([{"outcome": "bad"}], [{"outcome": "rendered", "first_render_ms": -1}], [{"outcome": "rendered"}] * 51):
            assert client.post("/v1/genui/telemetry", headers=AUTH, json=body).status_code == 422
        before = metrics.GENUI_CLIENT.labels("rendered")._value.get()
        for _ in range(10):
            assert (
                client.post(
                    "/v1/genui/telemetry",
                    headers=AUTH,
                    json=[{"outcome": "rendered", "component": "ArbitraryLabel", "first_render_ms": 250}],
                ).status_code
                == 204
            )
        response = client.post("/v1/genui/telemetry", headers=AUTH, json=[])
        assert response.status_code == 429 and int(response.headers["Retry-After"]) > 0
        assert metrics.GENUI_CLIENT.labels("rendered")._value.get() == before + 10
    telemetry_throttle._failures.clear()


def test_history_api_and_result_whitelist():
    with TestClient(app) as client:
        conv = client.post("/v1/conversations", headers=AUTH, json={}).json()["id"]
        ids = []
        for _ in range(3):
            run = client.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "test"}).json()["id"]
            ids.append(run)
            if len(ids) < 3:
                _finish(client, run, UI, genui={"format": "openui", "catalog": 2, "blocks": 1})
        detail = client.get(f"/v1/conversations/{conv}", headers=AUTH).json()
        assert detail["turns"][0]["result"]["genui"]["blocks"] == 1
        for endpoint in (f"/internal/runs/{ids[-1]}", f"/internal/runs/{ids[-1]}/model-context"):
            history = client.get(endpoint, headers=SERVICE).json()["history"]
            assert history[0]["reply"] == history[1]["reply"] == UI


@pytest.mark.parametrize("marker,blocks,unclosed", [("demo", 3, 0), ("bad", 2, 1), ("inject", 1, 0)])
def test_mock_markers_stream_fence_fragments(marker, blocks, unclosed):
    chunks = []

    async def sink(chunk):
        chunks.append(chunk)

    response = asyncio.run(MockLLM().chat_stream([{"role": "user", "content": f"[[genui-{marker}]]"}], [], "mock", on_text=sink))
    assert "".join(chunks) == response.content
    assert all(len(chunk) <= 2 for chunk in chunks)
    report = inspect(response.content)
    assert (report["blocks"], report["unclosed"]) == (blocks, unclosed)
    if marker == "inject":
        for payload in ("<script>", "<img onerror", "javascript:"):
            assert payload in response.content


def test_agent_completion_records_genui_without_new_events():
    from ailab_platform.llm import LLMResponse
    from tests.test_agent_loop import ScriptedLLM, _run

    platform, _ = _run(ScriptedLLM([LLMResponse(content=UI)]))
    status, payload = platform.statuses[-1]
    assert status == "succeeded"
    assert payload["summary"] == UI
    assert payload["result"]["genui"] == {"format": "openui", "catalog": 2, "blocks": 1}
    assert not any("genui" in name for name, _ in platform.events)


def test_title_input_strips_user_ui(monkeypatch):
    from ailab_platform.agent.loop import AgentLoop
    from ailab_platform.llm import LLMResponse
    from tests.test_agent_loop import FakePlatform

    seen = []

    async def call(*args, **kwargs):
        seen.append(args[4])
        return LLMResponse(content="测试标题")

    loop = AgentLoop(platform=FakePlatform(), llm=MockLLM())
    monkeypatch.setattr(loop, "_call_model", call)
    asyncio.run(loop._conversation_title("run", {"needs_conversation_title": True, "model": "m", "prompt": UI + "问题"}))
    assert seen
    assert "Metric" in seen[0][1]["content"]
    assert "root =" not in seen[0][1]["content"] and "```" not in seen[0][1]["content"]


def test_compaction_inputs_strip_ui_before_serializing(monkeypatch):
    from ailab_platform.agent.loop import AgentLoop, _summary_input
    from ailab_platform.catalog import Catalog, ModelSpec
    from ailab_platform.llm import LLMClient, LLMResponse
    from tests.test_agent_loop import FakePlatform

    seen = []

    class SummaryLLM(LLMClient):
        async def chat(self, messages, tools, model, user_id=None):
            seen.append(messages)
            assert "root =" not in json.dumps(messages, ensure_ascii=False)
            return LLMResponse(content="保留事实。", usage={"prompt_tokens": 50, "completion_tokens": 10})

    class Context(FakePlatform):
        async def get_model_context(self, run_id, after=None):
            return {
                "summary": "",
                "through_run_id": None,
                "history": [{"id": str(n), "prompt": "问题" * 100, "reply": "回答" * 100 + "\n" + UI} for n in range(15)],
                "has_more": False,
            }

        async def save_model_summary(self, run_id, payload):
            pass

    # Room for the fixed tool catalog (~2.8k tokens) plus a few turns, so the 15 turns still force compaction.
    cat = Catalog([ModelSpec("summary", "Summary", provider="mock", context_tokens=6000, max_output_tokens=1000)])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: cat)
    loop = AgentLoop(platform=Context(), llm=SummaryLLM())
    asyncio.run(
        loop._assemble_context(
            "run", {"_attempt_id": "attempt"}, "summary", [{"role": "system", "content": "系统"}, {"role": "user", "content": "本轮"}]
        )
    )
    assert seen
    assert "root =" not in json.dumps(_summary_input([{"content": [{"type": "text", "text": UI}]}]))


def test_search_summary_snippet_does_not_leak_ui_source():
    with TestClient(app) as client:
        conv = client.post("/v1/conversations", headers=AUTH, json={}).json()["id"]
        run = client.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "search fixture"}).json()["id"]
        _finish(client, run, "之前\n" + UI + "可搜索的结论")
        result = client.get("/v1/search", headers=AUTH, params={"q": "可搜索的结论"}).json()
        item = next(row for row in result["items"] if row["conversation_id"] == conv)
        assert "可搜索的结论" in item["snippet"]
        assert "root =" not in item["snippet"] and "```" not in item["snippet"]


def test_work_compaction_preserves_genui_prefix_in_summary_request(monkeypatch):
    from ailab_platform.adapters.runtime import fresh_state
    from ailab_platform.agent.loop import AgentLoop, _Turn
    from ailab_platform.catalog import Catalog, ModelSpec
    from ailab_platform.llm import LLMClient, LLMResponse
    from tests.test_agent_loop import FakeBroker, FakePlatform

    seen = []

    class SummaryLLM(LLMClient):
        async def chat(self, messages, tools, model, user_id=None):
            assert messages[1]["content"] == UI + "原始问题"
            assert "root =" in messages[2]["content"]
            assert "只输出摘要" in messages[-1]["content"]
            seen.append(messages)
            return LLMResponse(content="执行摘要", usage={"prompt_tokens": 10, "completion_tokens": 5})

    cat = Catalog([ModelSpec("m", "m", provider="mock", context_tokens=10000, max_output_tokens=1000)])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: cat)
    monkeypatch.setattr("ailab_platform.agent.loop.estimator.estimate", lambda model, value: 8500 if "OLDER" in json.dumps(value) else 100)
    loop = AgentLoop(FakePlatform(), FakeBroker(), SummaryLLM())
    state = fresh_state()
    state["messages"] = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": UI + "原始问题"},
        {"role": "assistant", "content": "OLDER\n" + UI},
        {"role": "assistant", "content": "recent one"},
        {"role": "assistant", "content": "recent two"},
    ]
    state["user_message_index"] = 1
    state["compaction"] = {"summary": UI}
    loop._states["r"], loop._steps["r"] = state, 0
    turn = _Turn({"prompt": UI + "原始问题", "_attempt_id": "a", "_ledger_tokens": 0})
    turn.messages = state["messages"]
    asyncio.run(loop._compact_work_context("r", turn, loop.llm, "m", []))
    assert seen


@pytest.mark.parametrize("html_enabled", [False, True])
@pytest.mark.parametrize("mode", ["chat", "work"])
def test_html_catalog_prompt_and_disabled_answer_metrics(monkeypatch, html_enabled, mode):
    from ailab_platform.llm import LLMResponse
    from tests.test_agent_loop import ScriptedLLM, _run

    monkeypatch.setattr(settings, "genui_enabled", True)
    monkeypatch.setattr(settings, "genui_html_enabled", html_enabled)
    assert ("HtmlArtifact" in system_prompt_for(mode, genui=True)) is html_enabled
    assert "HtmlArtifact" in component_names()
    answer = '```openui\nroot = HtmlArtifact("title", "<p>hello</p>", "hello")\n```'
    assert inspect(answer)["components"] == {"HtmlArtifact": 1}
    before = metrics.GENUI_COMPONENTS.labels("HtmlArtifact")._value.get()
    platform, _ = _run(ScriptedLLM([LLMResponse(content=answer)]))
    assert platform.statuses[-1][0] == "succeeded"
    assert platform.statuses[-1][1]["result"]["genui"]["blocks"] == 1
    assert metrics.GENUI_COMPONENTS.labels("HtmlArtifact")._value.get() == before + 1


@pytest.mark.parametrize("enabled,html_enabled", [(True, True), (True, False), (False, True), (False, False)])
def test_me_html_admission(monkeypatch, enabled, html_enabled):
    monkeypatch.setattr(settings, "genui_enabled", enabled)
    monkeypatch.setattr(settings, "genui_html_enabled", html_enabled)
    with TestClient(app) as client:
        response = client.get("/v1/me", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["features"] == {"genui_html": enabled and html_enabled}


def test_html_defaults_and_larger_block_history():
    config = Settings(_env_file=None)
    assert config.genui_html_enabled is True
    assert config.genui_max_block_chars == 32000
    answer = '```openui\nroot = Text("' + "x" * 20000 + '")\n```'
    assert inspect(answer)["oversize"] == 0
    assert inspect(answer, max_block_chars=16000)["oversize"] == 1
    assert for_history(answer, True, 32000) == answer
    assert "```" not in for_history(answer, True, 16000)


def test_html_environment_switch(monkeypatch):
    monkeypatch.setenv("AILAB_GENUI_HTML_ENABLED", "false")
    assert Settings(_env_file=None).genui_html_enabled is False


def test_a_later_turn_knows_which_pages_the_earlier_one_fetched():
    """Tool traffic is not replayed; without its sources a model reads its own cited numbers as invented ones."""
    pages = [{"title": "Introducing Opus", "url": "https://example.com/opus", "site": "example.com"}, {"url": "https://example.com/b"}]
    with TestClient(app) as client:
        conv = client.post("/v1/conversations", headers=AUTH, json={}).json()["id"]
        first = client.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "对比", "mode": "chat"}).json()["id"]
        _finish(client, first, UI, sources=pages)
        second = client.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "可视化呢？", "mode": "chat"}).json()["id"]
        for endpoint in (f"/internal/runs/{second}", f"/internal/runs/{second}/model-context"):
            history = client.get(endpoint, headers=SERVICE).json()["history"]
            assert history[-1]["sources"] == [
                {"title": "Introducing Opus", "url": "https://example.com/opus"},
                {"title": "", "url": "https://example.com/b"},
            ]
        reply = history_messages(history)[-1]["content"]
        assert reply.endswith(
            "（这一轮用网页搜索/读取工具获取过的来源：Introducing Opus https://example.com/opus；https://example.com/b https://example.com/b）"
        )


@pytest.mark.parametrize("html_enabled", [False, True])
def test_ui_components_progressive_docs(html_enabled):
    from ailab_platform.adapters.runtime import checkpoint_state, restored_state
    from ailab_platform.agent.tools import ui_components
    from ailab_platform.contracts.genui import docs_for

    state = {"runtime_version": 1, "messages": []}
    first = ui_components({"names": ["LineChart", "LineChart", "Missing", "HtmlArtifact"]}, state, html_enabled=html_enabled)
    assert first["guide"] == guide_for(html_enabled)
    expected, unknown = docs_for(["LineChart", "HtmlArtifact", "Missing"], html_enabled)
    assert first["components"] == expected
    assert set(first["unknown"]) == set(unknown) == ({"Missing"} if html_enabled else {"Missing", "HtmlArtifact"})
    assert ("HtmlArtifact" in first["available"]) is html_enabled
    assert "Series(" in first["components"]
    state["messages"] = _ui_docs_messages(first)
    restored = restored_state({"state": checkpoint_state(state, {})})
    second = ui_components({"names": ["Text"]}, restored, html_enabled=html_enabled)
    assert "guide" not in second and "available" not in second
    repeated = ui_components({"names": ["LineChart"]}, restored, html_enabled=html_enabled)
    assert repeated["components"] == "" and set(repeated["already_available"]) == {"LineChart", "Series"}
    # A new Run only carries messages, not any old boolean or tool log.
    carried = {"messages": restored["messages"]}
    added = ui_components({"names": ["BarChart"]}, carried, html_enabled=html_enabled)
    bar_docs, _ = docs_for(["BarChart"], html_enabled)
    series_docs, _ = docs_for(["Series"], html_enabled)
    assert added["components"] == bar_docs.replace(series_docs, "").strip()
    assert added["already_available"] == ["Series"] and "guide" not in added
    restored["messages"] = [{"role": "system", "content": "历史摘要：已读过LineChart"}]
    restored["genui_guide_sent"] = True  # an older checkpoint marker must not survive missing docs
    assert "guide" in ui_components({"names": ["LineChart"]}, restored, html_enabled=html_enabled)


def test_prompt_budget_and_words_only(monkeypatch):
    from ailab_platform.model_policy import estimator

    monkeypatch.setattr(settings, "genui_enabled", True)
    monkeypatch.setattr(settings, "genui_html_enabled", True)
    index = index_for(True)
    catalog = load_contract()[0]
    assert all(c["doc"] not in index for c in catalog["components"])
    prompt = system_prompt_for("chat", genui=True, deferred=True, genui_tools=True)
    assert estimator.raw(prompt) <= 2000
    full = system_prompt_for("chat", genui=True, deferred=True, genui_tools=False)
    assert full_prompt(True) in full
    assert all(c["doc"] in full for c in catalog["components"])


@pytest.mark.parametrize("args", [{}, {"names": []}, {"names": ["Text"] * 17}, {"names": [1]}, {"names": ["Text"], "extra": True}])
def test_ui_components_validates_arguments(args):
    from ailab_platform.agent.tools import ToolError, ui_components

    with pytest.raises(ToolError):
        ui_components(args, {}, html_enabled=False)


@pytest.mark.parametrize("mode", ["chat", "work"])
@pytest.mark.parametrize("supported", [False, True])
def test_ui_tool_model_admission_and_no_sandbox(monkeypatch, mode, supported):
    from ailab_platform.agent.tools import tools_for_chat, tools_for_turn
    from ailab_platform.catalog import Catalog, ModelSpec
    from ailab_platform.llm import LLMResponse, ToolCall
    from tests.test_agent_loop import FakePlatform, ScriptedLLM, _run

    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "options": {"mode": mode, "capability_discovery": 1}}

    capabilities = frozenset({"tools", "genui"} if supported else {"tools"})
    monkeypatch.setattr(
        "ailab_platform.agent.loop.get_catalog", lambda: Catalog([ModelSpec("m", "m", provider="mock", capabilities=capabilities)])
    )
    monkeypatch.setattr(settings, "genui_enabled", True)
    catalog = tools_for_chat() if mode == "chat" else tools_for_turn()
    assert "ui_components" in {t["function"]["name"] for t in catalog}
    llm = ScriptedLLM([LLMResponse(tool_calls=[ToolCall("u1", "ui_components", {"names": ["Text"]})]), LLMResponse(content="完成")])
    platform, broker = _run(llm, platform=Platform())
    assert platform.statuses[-1][0] == "succeeded"
    result = json.loads(next(m for m in llm.seen_messages[-1] if m["role"] == "tool")["content"])
    assert result["ok"] is supported
    assert broker.leases == 0


@pytest.mark.parametrize("kind", ["index", "guide", "format", "version", "name", "dependency", "duplicate", "group"])
def test_contract_v2_rejects_corruption(tmp_path, kind):
    catalog, index, guide = load_contract()
    catalog = json.loads(json.dumps(catalog))
    for label, text in (("index", index), ("guide", guide)):
        (tmp_path / f"openui-v2.{label}.md").write_text(text + ("tamper" if kind == label else ""))
    if kind == "format":
        catalog["format"] = "other"
    elif kind == "version":
        catalog["version"] = 1
    elif kind == "name":
        catalog["components"][0]["name"] = "bad(name)"
    elif kind == "dependency":
        catalog["components"][0]["needs"] = ["Missing"]
    elif kind == "duplicate":
        catalog["components"].append(catalog["components"][0])
    elif kind == "group":
        catalog["components"][0]["group"] = "missing group"
    (tmp_path / "catalog-v2.json").write_text(json.dumps(catalog))
    with pytest.raises(ValueError):
        load_contract(tmp_path)


def test_docs_follow_catalog_order_and_recursive_dependencies(monkeypatch):
    import ailab_platform.contracts.genui as genui

    entries = [
        {"name": "A", "doc": "A docs", "needs": ["B"]},
        {"name": "B", "doc": "B docs", "needs": ["C"]},
        {"name": "C", "doc": "C docs", "needs": ["A"]},
        {"name": "HtmlArtifact", "doc": "HTML docs", "needs": []},
    ]
    monkeypatch.setattr(genui, "_loaded", lambda: ({"components": entries}, "", ""))
    assert genui.docs_for(["C", "B", "C", "oops", "oops"], False) == ("A docs\n\nB docs\n\nC docs", ["oops"])


def _ui_docs_messages(result):
    return [
        {"role": "assistant", "tool_calls": [{"id": "docs", "type": "function", "function": {"name": "ui_components", "arguments": "{}"}}]},
        {"role": "tool", "name": "ui_components", "tool_call_id": "docs", "content": json.dumps(result, ensure_ascii=False)},
    ]


@pytest.mark.parametrize("alteration", ["old_version", "truncated", "failed", "user", "unpaired", "wrong_tool", "malformed"])
def test_ui_docs_reuse_requires_complete_trusted_current_results(alteration):
    from ailab_platform.agent.tools import ui_components

    first = ui_components({"names": ["Timeline"]}, {}, html_enabled=True)
    messages = _ui_docs_messages(first)
    if alteration in {"old_version", "truncated", "failed"}:
        changed = dict(first)
        if alteration == "old_version":
            changed["components"] = changed["components"].replace("Timeline(", "OldTimeline(")
            changed["guide"] = "旧版指南"
        elif alteration == "truncated":
            changed["components"] = changed["components"][:-1]
            changed["guide"] = changed["guide"][:-1]
        else:
            changed["ok"] = False
        messages[-1]["content"] = json.dumps(changed)
    elif alteration == "user":
        messages[-1]["role"] = "user"
    elif alteration == "unpaired":
        messages = messages[-1:]
    elif alteration == "wrong_tool":
        messages[-1]["name"] = "read_url"
    else:
        messages[-1]["content"] = "not json"
    result = ui_components({"names": ["Timeline"]}, {"messages": messages}, html_enabled=True)
    assert result["components"] == first["components"] and result["guide"] == first["guide"]
    assert "already_available" not in result


def test_ui_docs_surviving_compaction_still_reusable_but_missing_guide_is_reloaded():
    from ailab_platform.agent.tools import ui_components

    first = ui_components({"names": ["Text"]}, {}, html_enabled=False)
    first.pop("guide")
    messages = [{"role": "system", "content": "历史已压缩"}, *_ui_docs_messages(first)]
    result = ui_components({"names": ["Text", "HtmlArtifact", "Missing"]}, {"messages": messages}, html_enabled=False)
    assert result["components"] == "" and result["already_available"] == ["Text"]
    assert result["unknown"] == ["HtmlArtifact", "Missing"] and "HtmlArtifact" not in result["available"]
    assert result["guide"] == guide_for(False)
