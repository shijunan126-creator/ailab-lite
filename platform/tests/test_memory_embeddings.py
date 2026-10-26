"""The dense channel of history recall, against a fake OpenAI-compatible embeddings endpoint.

The fake maps text to a handful of concept dimensions, so a paraphrase with no shared words ("容器编排" for "Kubernetes")
is close in vector space and far in BM25: exactly the case the dense channel is for.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from ailab_platform.api.main import app
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.memory_recall import embeddings
from ailab_platform.memory_recall.lineage import purge_episodes_for_runs
from ailab_platform.memory_recall.search import NO_RELEVANT_MEMORY, search_history
from ailab_platform.models import Conversation, MemoryEmbedding, Run, User

from .test_memory_recall import _conversation, _seed, _uid
from .test_skill_settings import _user

CONCEPTS = [
    ("kubernetes", "k8s", "容器编排"),
    ("周报", "汇报文档", "report"),
    ("天气", "weather"),
    ("东京", "出差", "tokyo"),
]


def _vector(text: str) -> list[float]:
    low = text.casefold()
    return [1.0 if any(w in low for w in group) else 0.0 for group in CONCEPTS]


class Fake:
    def __init__(self) -> None:
        self.fail = False
        self.calls: list[dict] = []
        self.auth: list[str | None] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.calls.append(body)
                fake.auth.append(self.headers.get("Authorization"))
                if fake.fail:
                    self.send_response(503)
                    self.end_headers()
                    return
                data = [{"index": i, "embedding": _vector(t)} for i, t in enumerate(body["input"])]
                payload = json.dumps({"data": data}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"


@pytest.fixture
def fake(monkeypatch):
    server = Fake()
    monkeypatch.setattr(settings, "memory_embedding_base_url", server.url)
    monkeypatch.setattr(settings, "memory_embedding_model", "fake-model")
    monkeypatch.setattr(settings, "memory_embedding_query_prefix", "")
    monkeypatch.setattr(settings, "memory_embedding_min_similarity", 0.5)
    yield server
    server.server.shutdown()


def _search(uid, query, **kw):
    with SessionLocal() as db:
        return search_history(db, db.get(User, uid), query, **kw)


def _stored(uid) -> set[str]:
    with SessionLocal() as db:
        return {r for (r,) in db.query(MemoryEmbedding.run_id).filter(MemoryEmbedding.user_id == uid)}


def test_a_paraphrase_with_no_shared_words_is_found_by_meaning(fake) -> None:
    with TestClient(app) as c:
        uid = _uid(c, _user(c))
        k8s = _seed(uid, "沙箱方案怎么选", "我们选了 Kubernetes，因为空闲沙箱必须销毁", days_ago=3)
        _seed(uid, "帮我整理下周计划", "好的，已整理", days_ago=2)
        assert embeddings.index_pending(user_id=uid) == 2
        out = _search(uid, "容器编排那件事为什么这么定")
        assert [r["run_id"] for r in out["results"]] == [k8s]
        assert out["results"][0]["found_by"] == ["dense"] and out["dense"] == "on"
        assert out["results"][0]["similarity"] >= 0.5


def test_without_an_endpoint_the_same_question_finds_nothing_lexically(monkeypatch) -> None:
    monkeypatch.setattr(settings, "memory_embedding_base_url", "")
    with TestClient(app) as c:
        uid = _uid(c, _user(c))
        _seed(uid, "沙箱方案怎么选", "我们选了 Kubernetes，因为空闲沙箱必须销毁", days_ago=3)
        out = _search(uid, "容器编排那件事为什么这么定")
        assert out["results"] == [] and out["note"] == NO_RELEVANT_MEMORY and out["dense"] == "off"


def test_an_unrelated_question_still_abstains_with_the_dense_channel_on(fake) -> None:
    with TestClient(app) as c:
        uid = _uid(c, _user(c))
        _seed(uid, "沙箱方案怎么选", "我们选了 Kubernetes", days_ago=3)
        embeddings.index_pending(user_id=uid)
        assert _search(uid, "今天天气怎么样")["results"] == []


def test_a_turn_found_by_both_channels_outranks_one_found_by_either(fake) -> None:
    with TestClient(app) as c:
        uid = _uid(c, _user(c))
        both = _seed(uid, "东京出差的行李清单", "护照、充电器", days_ago=5)
        lexical_only = _seed(uid, "出差报销流程", "发票要贴好", days_ago=1)
        dense_only = _seed(uid, "tokyo trip packing", "passport and charger", days_ago=9)
        embeddings.index_pending(user_id=uid)
        out = _search(uid, "东京出差")
        ids = [r["run_id"] for r in out["results"]]
        assert ids[0] == both and set(ids) >= {both, lexical_only, dense_only}
        assert out["results"][0]["found_by"] == ["lexical", "dense"]


def test_when_the_endpoint_is_down_recall_falls_back_to_lexical(fake) -> None:
    with TestClient(app) as c:
        uid = _uid(c, _user(c))
        run = _seed(uid, "周报格式：先结论后风险", "好的", days_ago=2)
        embeddings.index_pending(user_id=uid)
        fake.fail = True
        out = _search(uid, "周报格式 结论")
        assert [r["run_id"] for r in out["results"]] == [run] and out["dense"] == "unavailable"
        _seed(uid, "新的一轮", "ok", days_ago=1)
        with pytest.raises(embeddings.EmbeddingError):  # the indexer reports it; the loop logs it and tries again next tick
            embeddings.index_pending(user_id=uid)


def test_the_indexer_skips_what_memory_must_not_use(fake) -> None:
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        kept = _seed(uid, "周报写法", "先结论", days_ago=1)
        gone = _conversation(uid)
        _seed(uid, "已删除对话里的周报", "ok", days_ago=1, conversation_id=gone)
        old = _seed(uid, "旧版周报", "ok", days_ago=1)
        with SessionLocal() as db:
            db.get(Conversation, gone).deleted_at = datetime.now(UTC)
            db.get(Run, old).superseded_by_run_id = "run_replacement"
            db.commit()
        assert embeddings.index_pending(user_id=uid) == 1
        assert _stored(uid) == {kept} and embeddings.index_pending(user_id=uid) == 0  # and it is done once
        sent = json.dumps(fake.calls, ensure_ascii=False)
        assert "已删除对话" not in sent and "旧版周报" not in sent
        # a user who turned memory off is not sent to the endpoint at all
        off = _user(c)
        off_uid = _uid(c, off)
        c.put("/v1/memory", headers=off, json={"expected_revision": 0, "enabled": False, "auto_save": True, "entries": []})
        _seed(off_uid, "不该被发送的周报", "ok", days_ago=1)
        assert embeddings.index_pending(user_id=off_uid) == 0 and "不该被发送" not in json.dumps(fake.calls, ensure_ascii=False)


def test_vectors_follow_the_model_and_never_cross_users(fake, monkeypatch) -> None:
    with TestClient(app) as c:
        mine, other = _uid(c, _user(c)), _uid(c, _user(c))
        _seed(mine, "沙箱方案", "选了 Kubernetes", days_ago=2)
        _seed(other, "另一个人的沙箱方案", "选了 Kubernetes", days_ago=2)
        embeddings.index_pending(user_id=mine)
        embeddings.index_pending(user_id=other)
        assert len(_search(mine, "容器编排")["results"]) == 1  # not two: the other user's turn is invisible
        monkeypatch.setattr(settings, "memory_embedding_model", "another-model")
        assert _search(mine, "容器编排")["results"] == []  # old vectors are another model's: ignored until re-indexed
        assert embeddings.index_pending(user_id=mine) == 1
        assert len(_search(mine, "容器编排")["results"]) == 1


def test_deleting_a_conversation_or_purging_runs_removes_their_vectors(fake) -> None:
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        conv = c.post("/v1/conversations", headers=me, json={}).json()["id"]
        in_conv = _seed(uid, "东京出差", "ok", days_ago=1, conversation_id=conv)
        loose = _seed(uid, "东京出差二", "ok", days_ago=1)
        embeddings.index_pending(user_id=uid)
        assert _stored(uid) == {in_conv, loose}
        assert c.delete(f"/v1/conversations/{conv}", headers=me).status_code == 204
        assert _stored(uid) == {loose}
        with SessionLocal() as db:
            assert purge_episodes_for_runs(db, [loose]) == 0  # no episode, but the vector still goes
            db.commit()
        assert _stored(uid) == set()


def test_the_endpoint_gets_the_model_name_the_key_and_the_query_prefix(fake, monkeypatch) -> None:
    monkeypatch.setattr(settings, "memory_embedding_api_key", "k-test")
    monkeypatch.setattr(settings, "memory_embedding_query_prefix", "Q: ")
    embeddings.embed_query("东京")
    assert fake.calls[-1] == {"model": "fake-model", "input": ["Q: 东京"]} and fake.auth[-1] == "Bearer k-test"
    with pytest.raises(embeddings.EmbeddingError):
        fake.fail = True
        embeddings.embed(["x"], timeout=2)


def test_calibration_picks_a_floor_between_the_groups_and_flags_an_overlap() -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("calibrate", Path(__file__).parents[1] / "scripts" / "calibrate_embedding.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clean = module.suggest([0.45, 0.55, 0.62], [0.2, 0.33, 0.36])
    assert clean["clean_split"] and clean["suggested_min_similarity"] == 0.41 and clean["related_kept"] == "3/3"
    overlap = module.suggest([0.30, 0.55, 0.62], [0.2, 0.33, 0.36])
    assert not overlap["clean_split"] and overlap["suggested_min_similarity"] == 0.37 and overlap["related_kept"] == "2/3"
    assert module.cosine([1, 0], [1, 1]) == pytest.approx(0.7071, abs=1e-3)


def test_the_dense_channel_stays_off_until_a_model_name_is_set(monkeypatch) -> None:
    monkeypatch.setattr(settings, "memory_embedding_base_url", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(settings, "memory_embedding_model", "")
    assert not embeddings.enabled() and embeddings.embed_query("x") is None and embeddings.index_pending() == 0


def test_embed_requests_bypass_the_environment_proxy(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(embeddings.httpx, "post", fake_post)
    assert embeddings.embed(["正文"], timeout=1) == [[1.0, 0.0]]
    assert captured["trust_env"] is False
