"""Synthetic regressions for memory v2; never contains user history."""

from ailab_platform.memory_recall.text import query_tokens
from ailab_platform.memory_dream.service import _assistant_context


def test_query_cues_preserve_word_boundaries():
    for word in ("weekly", "power", "youtube", "answer"):
        assert word in query_tokens(word)
    assert "我国" in query_tokens("我国经济")
    assert "report" in query_tokens("we discussed weekly report")
    assert "we" not in query_tokens("we discussed weekly report")


def test_extraction_context_is_short_and_has_no_code_or_images():
    assert _assistant_context("说明\n```openui\nsecret_dsl\n```\n![图](image_123)\n结束") == "说明\n\n\n结束"
    assert len(_assistant_context("长" * 1000)) == 300


def test_view_items_revision_and_delete_contract():
    from fastapi.testclient import TestClient
    from ailab_platform.api.main import app
    from tests.test_skill_settings import _user
    from tests.test_memory_hardening import put

    with TestClient(app) as client:
        headers = _user(client)
        saved = put(
            client,
            headers,
            [
                {"kind": "user", "content": "长期偏好简洁中文", "tier": "core", "page": "偏好"},
                {"kind": "memory", "content": "敏感待确认", "status": "review", "tier": "core", "page": "工作"},
            ],
        )
        view = client.get("/v1/memory/view", headers=headers).json()
        assert len(view["core"]) == 1
        item_id = view["core"][0]["id"]
        items = client.get("/v1/memory/items", headers=headers).json()["items"]
        assert {i["status"] for i in items} == {"active", "review"}
        assert (
            client.patch(f"/v1/memory/items/{item_id}", headers=headers, json={"expected_revision": 0, "tier": "archive"}).status_code
            == 409
        )
        assert (
            client.patch(
                f"/v1/memory/items/{item_id}", headers=headers, json={"expected_revision": saved["revision"], "tier": "archive"}
            ).status_code
            == 200
        )
        view = client.get("/v1/memory/view", headers=headers).json()
        assert view["core"] == []
        assert client.delete(f"/v1/memory/items/{item_id}?expected_revision={view['revision']}", headers=headers).status_code == 200
        assert not any(i["id"] == item_id for i in client.get("/v1/memory/items", headers=headers).json()["items"])


def test_archive_not_in_standing_prompt():
    from ailab_platform.agent.prompts import memory_prompt

    prompt = memory_prompt(
        {
            "entries": [
                {
                    "id": "mem_synthetic",
                    "kind": "fact",
                    "tier": "archive",
                    "page": "项目/Atlas",
                    "content": "UNIQUE_ARCHIVE_BODY",
                    "entities": ["Atlas"],
                }
            ]
        }
    )
    assert "Atlas" in prompt
    assert "UNIQUE_ARCHIVE_BODY" not in prompt
