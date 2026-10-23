"""Bounded chronological pages and confirmed-result titles."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import Conversation, Run
from tests.run_claims import SERVICE
from tests.test_conversations import AUTH, _finish


def test_pages_use_timestamp_and_id_and_reject_foreign_cursor():
    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        other = c.post("/v1/conversations", headers=AUTH, json={}).json()
        with SessionLocal() as db:
            for i in range(45):
                db.add(
                    Run(
                        id=f"page_{i:03}",
                        user_id=db.get(Conversation, conv["id"]).user_id,
                        conversation_id=conv["id"],
                        prompt=str(i),
                        model="test-fast",
                        status="succeeded",
                        created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    )
                )
            db.commit()
        url = f"/v1/conversations/{conv['id']}"
        page = c.get(url, headers=AUTH).json()
        assert len(page["turns"]) == 20 and page["has_more"] is True
        assert [r["prompt"] for r in page["turns"]] == [str(i) for i in range(25, 45)]
        two = c.get(url, headers=AUTH, params={"before": page["turns"][0]["id"], "limit": 20}).json()
        assert [r["prompt"] for r in two["turns"]] == [str(i) for i in range(5, 25)]
        three = c.get(url, headers=AUTH, params={"before": two["turns"][0]["id"]}).json()
        assert len(three["turns"]) == 5 and three["has_more"] is False
        assert c.get(f"/v1/conversations/{other['id']}?before=page_000", headers=AUTH).status_code == 404
        assert c.get(url + "?before=missing", headers=AUTH).status_code == 404
        assert c.get(url + "?limit=101", headers=AUTH).status_code == 422


def test_title_uses_confirmed_model_heading_and_preserves_manual_title():
    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        url = f"/v1/conversations/{conv['id']}"
        run = c.post(url + "/messages", headers=AUTH, json={"prompt": "帮我想想怎么做"}).json()
        assert c.get(url, headers=AUTH).json()["title"] == ""
        _finish(c, run["id"], "# 城市更新投资分析\n\n以下是分析。")
        assert c.get(url, headers=AUTH).json()["title"] == "城市更新投资分析"
        c.patch(url, headers=AUTH, json={"title": "我自己的标题"})
        run = c.post(url + "/messages", headers=AUTH, json={"prompt": "继续"}).json()
        _finish(c, run["id"], "# 新分析", conversation_title="模型建议标题")
        assert c.get(url, headers=AUTH).json()["title"] == "我自己的标题"
        assert c.patch(url, headers=AUTH, json={"title": "   "}).status_code == 422


def test_auto_title_candidates_are_confirmed_bounded_and_optional():
    with TestClient(app) as c:
        for summary, result, expected in [
            ("普通回答不会被截断冒充标题。", {}, ""),
            ("```md\n# 示例标题\n```\n普通回答", {}, ""),
            ("# 总结\n答案", {}, ""),
            ("普通回答", {"conversation_title": "多区域销售预测"}, "多区域销售预测"),
            ("普通回答", {"conversation_title": "长" * 41}, ""),
            ("普通回答", {"conversation_title": ["坏数据"]}, ""),
        ]:
            conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
            url = f"/v1/conversations/{conv['id']}"
            run = c.post(url + "/messages", headers=AUTH, json={"prompt": "请分析"}).json()
            _finish(c, run["id"], summary, **result)
            assert c.get(url, headers=AUTH).json()["title"] == expected


def test_500_turn_first_page_is_bounded_and_fast():
    import statistics
    import time

    from sqlalchemy import select, text

    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        with SessionLocal() as db:
            owner = db.get(Conversation, conv["id"]).user_id
            db.add_all(
                [
                    Run(
                        user_id=owner,
                        conversation_id=conv["id"],
                        prompt=str(i),
                        model="test-fast",
                        status="succeeded",
                        result={"private_large_data": "x" * 10000},
                    )
                    for i in range(500)
                ]
            )
            db.commit()
            query = (
                select(Run)
                .where(Run.conversation_id == conv["id"], Run.user_id == owner)
                .order_by(Run.created_at.desc(), Run.id.desc())
                .limit(21)
            )
            sql = str(query.compile(db.bind, compile_kwargs={"literal_binds": True}))
            prefix = "EXPLAIN QUERY PLAN " if db.bind.dialect.name == "sqlite" else "EXPLAIN "
            plan = db.execute(text(prefix + sql)).all()
            print("PAGING PLAN", db.bind.dialect.name, plan)
        timings = []
        for _ in range(25):
            start = time.perf_counter()
            response = c.get(f"/v1/conversations/{conv['id']}", headers=AUTH)
            timings.append((time.perf_counter() - start) * 1000)
            assert len(response.json()["turns"]) == 20
            assert "private_large_data" not in response.text
        p95 = statistics.quantiles(timings, n=20)[18]
        print(f"PAGING 500 turns p95={p95:.2f}ms max={max(timings):.2f}ms bytes={len(response.content)}")
        assert p95 < 150


def test_title_database_failure_does_not_fail_the_confirmed_answer(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    from ailab_platform.api import routes_internal

    def fail(*args):
        raise SQLAlchemyError("simulated title write failure")

    monkeypatch.setattr(routes_internal, "apply_result_title", fail)
    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "你好"}).json()
        finished = _finish(c, run["id"], "# 结果标题\n回答内容")
        assert finished["status"] == "succeeded"
        assert finished["summary"] == "# 结果标题\n回答内容"


def test_title_request_flag_is_internal_and_rechecked_after_manual_rename():

    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "普通聊天"}).json()
        internal = f"/internal/runs/{run['id']}"
        assert c.get(internal, headers=SERVICE).json()["needs_conversation_title"] is True
        assert "needs_conversation_title" not in c.get(f"/v1/runs/{run['id']}", headers=AUTH).json()
        c.patch(f"/v1/conversations/{conv['id']}", headers=AUTH, json={"title": "手动命名"})
        assert c.get(internal, headers=SERVICE).json()["needs_conversation_title"] is False
        _finish(c, run["id"], "普通文本回答", conversation_title="模型标题")
        assert c.get(f"/v1/conversations/{conv['id']}", headers=AUTH).json()["title"] == "手动命名"
        standalone = c.post("/v1/runs", headers=AUTH, json={"prompt": "无会话任务"}).json()
        assert c.get(f"/internal/runs/{standalone['id']}", headers=SERVICE).json()["needs_conversation_title"] is False


def test_applying_title_does_not_commit_the_callers_transaction():
    from ailab_platform.conversation_titles import apply_result_title

    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "普通聊天"}).json()
        _finish(c, run["id"], "无主标题回答")
        with SessionLocal() as db:
            stored = db.get(Run, run["id"])
            stored.result = {"conversation_title": "事务内的标题"}
            db.flush()
            apply_result_title(db, stored)
            assert db.get(Conversation, conv["id"]).title == "事务内的标题"
            db.rollback()
        with SessionLocal() as db:
            assert db.get(Conversation, conv["id"]).title == ""
            assert not db.get(Run, run["id"]).result.get("conversation_title")


def test_agent_transition_applies_optional_title_in_same_commit():
    from tests.run_claims import attempt_headers

    with TestClient(app) as client:
        conversation = client.post("/v1/conversations", headers=AUTH, json={}).json()
        run = client.post(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH, json={"prompt": "普通文本回答也要标题"}).json()
        path = f"/internal/runs/{run['id']}/transition"
        owner = attempt_headers(client, run["id"])
        assert client.post(path, headers=owner, json={"status": "running"}).status_code == 200
        response = client.post(
            path,
            headers=owner,
            json={
                "status": "succeeded",
                "summary": "这里是一段没有 Markdown 标题的普通回答。",
                "result": {"conversation_title": "普通聊天的自动标题"},
            },
        )
        assert response.status_code == 200, response.text
        assert client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH).json()["title"] == "普通聊天的自动标题"
