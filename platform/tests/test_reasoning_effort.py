"""Thinking strength (思考强度): reasoning.toml profiles, what runs store and what reaches the upstream."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ailab_platform import catalog as catalog_mod
from ailab_platform.api.main import app
from ailab_platform.catalog import CatalogError, load_catalog, parse_catalog, parse_reasoning
from ailab_platform.config import settings
from ailab_platform.llm import ModelCall, model_call, reset_llm_cache
from ailab_platform.model_transport import ModelTransport
from tests.test_conversations import _finish

AUTH = {"Authorization": "Bearer test-token"}
PLATFORM = Path(__file__).resolve().parents[1]

PROFILE = """
[think]
default = "low"
levels = [
  { id = "low", name = "低", body = { reasoning_effort = "low" } },
  { id = "high", name = "高", body = { reasoning_effort = "high" } },
]
"""


def test_shipped_deepseek_profile_defaults_to_low_and_maps_to_real_strengths() -> None:
    cat = load_catalog(PLATFORM / "models.toml")
    profile = cat.reasoning_for(cat.get("deepseek-v4.1-flash"))
    assert profile is not None and profile.default == "low"
    assert [(x.id, x.body) for x in profile.levels] == [
        ("low", {"reasoning_effort": "low"}),
        ("high", {"reasoning_effort": "high"}),
        ("max", {"reasoning_effort": "max"}),
    ]
    assert cat.reasoning_for(cat.get("ailab-demo")) is None


@pytest.mark.parametrize(
    "data,message",
    [
        ({"p": {"default": "a", "levels": []}}, "1-8 levels"),
        ({"p": {"default": "x", "levels": [{"id": "a", "name": "A", "body": {"e": 1}}]}}, "default 'x'"),
        ({"p": {"default": "a", "levels": [{"id": "a", "name": "A", "body": {"e": 1}}] * 2}}, "duplicate"),
        ({"p": {"default": "a", "levels": [{"id": "a", "name": "A", "body": {"max_tokens": 1}}]}}, "framing"),
        ({"p": {"default": "a", "levels": [{"id": "a", "name": "A", "body": {}}]}}, "non-empty"),
        ({"p": {"default": "a", "levels": [{"id": "a", "name": "", "body": {"e": 1}}]}}, "name of 1-12"),
        ({"p": {"default": "a", "levels": [{"id": "a", "name": "A"}]}}, "exactly id, name and body"),
        ({"p": {"default": "a", "levels": [{"id": "a", "name": "A", "body": {"e": 1}}], "extra": 1}}, "unknown keys"),
    ],
)
def test_bad_profiles_are_refused(data, message) -> None:
    with pytest.raises(CatalogError, match=message):
        parse_reasoning(data)


def test_a_model_naming_a_missing_profile_is_refused(tmp_path) -> None:
    with pytest.raises(CatalogError, match="unknown reasoning profile"):
        parse_catalog({"models": [{"id": "m", "provider": "mock", "reasoning": "nope"}]}, {})
    (tmp_path / "models.toml").write_text('[[models]]\nid = "m"\nprovider = "mock"\nreasoning = "think"\n')
    (tmp_path / "reasoning.toml").write_text(PROFILE.replace("[think]", "[other]"))
    with pytest.raises(CatalogError, match="unknown reasoning profile"):
        load_catalog(tmp_path / "models.toml")


def _transport(**kw) -> ModelTransport:
    profile = parse_reasoning(__import__("tomllib").loads(PROFILE))["think"]
    return ModelTransport("http://gw.test/v1", reasoning=profile, **kw)


def _payload(transport: ModelTransport, call: ModelCall | None) -> dict:
    token = model_call.set(call)
    try:
        return transport._request([{"role": "user", "content": "hi"}], [], "m", None, True)[0]
    finally:
        model_call.reset(token)


def test_each_call_carries_its_runs_level_and_side_calls_keep_it() -> None:
    transport = _transport(disable_reasoning_for_quiet=True)
    assert _payload(transport, ModelCall("c", "r", 100, reasoning="high"))["reasoning_effort"] == "high"
    # a summary continues the run's prefix: same level, so the provider's cache still applies
    summary = _payload(transport, ModelCall("c", "r", 100, quiet=True, continues_prefix=True, reasoning="high"))
    assert summary["reasoning_effort"] == "high" and "thinking" not in summary
    # a title switches thinking off and so has no level
    title = _payload(transport, ModelCall("c", "r", 100, quiet=True, reasoning="high"))
    assert title["thinking"] == {"type": "disabled"} and "reasoning_effort" not in title
    # older runs and a fallback model without the run's level use the profile default
    assert _payload(transport, ModelCall("c", "r", 100))["reasoning_effort"] == "low"
    assert _payload(transport, ModelCall("c", "r", 100, reasoning="ultra"))["reasoning_effort"] == "low"
    # unmetered callers and models without a profile send nothing
    assert "reasoning_effort" not in _payload(transport, None)
    assert "reasoning_effort" not in _payload(ModelTransport("http://gw.test/v1"), ModelCall("c", "r", 100, reasoning="high"))


@pytest.fixture()
def thinking_catalog(tmp_path, monkeypatch):
    shipped = (Path(__file__).parent / "models.test.toml").read_text()
    (tmp_path / "models.toml").write_text(shipped.replace('id = "test-fast"\n', 'id = "test-fast"\nreasoning = "think"\n'))
    (tmp_path / "reasoning.toml").write_text(PROFILE)
    monkeypatch.setattr(settings, "models_file", tmp_path / "models.toml")
    catalog_mod.get_catalog.cache_clear()
    reset_llm_cache()
    yield
    monkeypatch.undo()
    catalog_mod.get_catalog.cache_clear()
    reset_llm_cache()


@pytest.fixture()
def api(thinking_catalog):
    with TestClient(app) as c:
        yield c


def test_models_endpoint_offers_levels_only_for_models_with_a_profile(api) -> None:
    models = {m["id"]: m for m in api.get("/v1/models", headers=AUTH).json()["models"]}
    assert models["test-fast"]["reasoning"] == {"default": "low", "levels": [{"id": "low", "name": "低"}, {"id": "high", "name": "高"}]}
    assert "reasoning" not in models["test-pro"]
    assert "reasoning_effort" not in json.dumps(models)  # the upstream fields stay on the server


def test_runs_store_the_chosen_level_or_the_default(api) -> None:
    chosen = api.post("/v1/runs", headers=AUTH, json={"prompt": "x", "model": "test-fast", "reasoning": "high"}).json()
    default = api.post("/v1/runs", headers=AUTH, json={"prompt": "x", "model": "test-fast"}).json()
    plain = api.post("/v1/runs", headers=AUTH, json={"prompt": "x", "model": "test-pro"}).json()
    assert chosen["options"]["reasoning"] == "high" and default["options"]["reasoning"] == "low"
    assert "reasoning" not in plain["options"]


@pytest.mark.parametrize("model,level,message", [("test-fast", "max", "思考强度无效"), ("test-pro", "low", "不能选择思考强度")])
def test_unknown_levels_are_refused(api, model, level, message) -> None:
    resp = api.post("/v1/runs", headers=AUTH, json={"prompt": "x", "model": model, "reasoning": level})
    assert resp.status_code == 400 and message in resp.json()["detail"]


def test_conversation_turns_retry_and_edit_keep_or_change_the_level(api) -> None:
    started = api.post("/v1/conversations/start", headers=AUTH, json={"prompt": "x", "model": "test-fast", "reasoning": "high"})
    assert started.status_code == 201, started.text
    run = started.json()["run"]
    assert run["options"]["reasoning"] == "high"
    _finish(api, run["id"], "done")
    retried = api.post(f"/v1/runs/{run['id']}/retry", headers=AUTH, json={}).json()
    assert retried["options"]["reasoning"] == "high"  # same model, no new choice: the old level
    _finish(api, retried["id"], "done")
    edited = api.post(f"/v1/runs/{retried['id']}/edit", headers=AUTH, json={"prompt": "y", "reasoning": "low"}).json()
    assert edited["options"]["reasoning"] == "low"
    _finish(api, edited["id"], "done")
    moved = api.post(f"/v1/runs/{edited['id']}/retry", headers=AUTH, json={"model": "test-pro"}).json()
    assert "reasoning" not in moved["options"]  # a model without levels drops it rather than failing


def test_a_queued_message_keeps_its_level_until_it_runs(api) -> None:
    run = api.post("/v1/conversations/start", headers=AUTH, json={"prompt": "x", "model": "test-fast"}).json()["run"]
    body = {"prompt": "next", "model": "test-fast", "reasoning": "high"}
    queued = api.post(f"/v1/conversations/{run['conversation_id']}/queue", headers=AUTH, json=body)
    assert queued.status_code == 201, queued.text
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import QueuedMessage

    with SessionLocal() as db:
        assert db.get(QueuedMessage, queued.json()["id"]).options["reasoning"] == "high"
    _finish(api, run["id"], "done")
    from sqlalchemy import select

    from ailab_platform.models import Run

    with SessionLocal() as db:
        nxt = db.scalar(select(Run).where(Run.conversation_id == run["conversation_id"], Run.id != run["id"]))
        assert nxt is not None and nxt.options["reasoning"] == "high"
