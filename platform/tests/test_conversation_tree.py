"""Public branching and ancestor-only model/workspace/summary contracts."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import Conversation, ModelUsage, OutboxMessage, Run
from tests.run_claims import SERVICE, attempt_headers
from tests.test_chat_fork_retry import create
from tests.test_conversations import AUTH, _finish, _other_user


def detail(c, conv, **params):
    response = c.get(f"/v1/conversations/{conv['id']}", headers=AUTH, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_edit_earlier_path_versions_switch_restore_and_fork():
    with TestClient(app) as c:
        conv, turns = create(c, ("work", "chat", "chat"))
        root, middle, leaf = [r["id"] for r in turns]
        with SessionLocal() as db:
            db.get(Run, root).result = {"workspace_saved": True, "previews": [{"filename": "base"}]}
            db.get(Run, middle).result = {"workspace_saved": True, "previews": [{"filename": "abandoned"}]}
            db.get(Conversation, conv["id"]).summary = "wrong branch"
            db.get(Conversation, conv["id"]).summary_through_run_id = middle
            db.commit()
        response = c.post(f"/v1/runs/{middle}/edit", headers=AUTH, json={"prompt": "edited question", "file_ids": None})
        assert response.status_code == 201, response.text
        edited = response.json()
        assert edited["edited_from"] == middle and edited["parent_run_id"] == root and edited["retry_of"] is None
        d = detail(c, conv, limit=1)
        assert d["has_more"] and d["turns"][0]["id"] == edited["id"]
        assert d["turns"][0]["versions"] == {"index": 2, "count": 2, "ids": [middle, edited["id"]]}
        assert [p["filename"] for p in d["previews"]] == ["base"]
        assert detail(c, conv, before=edited["id"])["turns"][0]["id"] == root
        assert c.get(f"/v1/conversations/{conv['id']}?before={leaf}", headers=AUTH).status_code == 404
        for endpoint in ("model-input", ""):
            path = f"/internal/runs/{edited['id']}" + (f"/{endpoint}" if endpoint else "")
            assert c.get(path, headers=SERVICE).json()["workspace_restore_key"] == f"workspaces/{root}.tgz"
        context = c.get(f"/internal/runs/{edited['id']}/model-context", headers=SERVICE).json()
        assert context["summary"] == "" and [r["id"] for r in context["history"]] == [root]
        assert c.post(f"/v1/conversations/{conv['id']}/active", headers=AUTH, json={"run_id": middle}).status_code == 409
        _finish(c, edited["id"], "new answer")
        c.post(f"/v1/conversations/{conv['id']}/active", headers=AUTH, json={"run_id": middle})
        assert detail(c, conv)["active_run_id"] == leaf
        assert [r["id"] for r in detail(c, conv)["turns"]] == [root, middle, leaf]
        # Switching root chooses the newest child at each level, hence the edited branch.
        selected = c.post(f"/v1/conversations/{conv['id']}/active", headers=AUTH, json={"run_id": root}).json()
        assert selected["active_run_id"] == edited["id"]
        # Fork an inactive chat branch; the work ancestor requires work and retains parent mapping.
        with SessionLocal() as db:
            db.get(Run, root).result = {}
            db.get(Run, middle).result = {}
            db.commit()
        fork = c.post(f"/v1/conversations/{conv['id']}/fork", headers=AUTH, json={"run_id": leaf, "mode": "work"})
        assert fork.status_code == 201, fork.text
        copied = detail(c, fork.json())["turns"]
        assert [r["prompt"] for r in copied] == [r["prompt"] for r in turns]
        assert copied[0]["parent_run_id"] is None and copied[1]["parent_run_id"] == copied[0]["id"]
        assert c.get(f"/v1/runs/{middle}", headers=AUTH).json()["summary"] == "answer-1"


def test_summary_cache_boundary_never_leaks_and_recomputes_after_switch():
    with TestClient(app) as c:
        conv, turns = create(c, ("chat", "chat"))
        root, old = [r["id"] for r in turns]
        edited = c.post(f"/v1/runs/{old}/edit", headers=AUTH, json={"prompt": "new"}).json()
        _finish(c, edited["id"], "different answer")
        next_run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "next"}).json()
        headers = attempt_headers(c, next_run["id"])
        with SessionLocal() as db:
            db.get(Conversation, conv["id"]).summary = "off branch secret"
            db.get(Conversation, conv["id"]).summary_through_run_id = old
            db.commit()
        url = f"/internal/runs/{next_run['id']}"
        context = c.get(url + "/model-context", headers=SERVICE).json()
        assert context["summary"] == "" and [r["id"] for r in context["history"]] == [root, edited["id"]]
        assert c.get(url + "/model-context", headers=SERVICE, params={"after": old}).status_code == 422
        invalid = {"summary": "bad", "through_run_id": old}
        assert c.put(url + "/model-summary", headers=headers, json=invalid).status_code == 422
        valid = {"summary": "valid new path", "through_run_id": edited["id"], "expected_through_run_id": None}
        assert c.put(url + "/model-summary", headers=headers, json=valid).status_code == 200
        assert c.get(url + "/model-context", headers=SERVICE).json()["summary"] == "valid new path"
        assert c.put(url + "/model-summary", headers=headers, json=valid).status_code == 422


@pytest.mark.parametrize("operation", ["send", "edit", "switch"])
def test_concurrent_path_mutations_are_serialized(operation):
    with TestClient(app) as c:
        conv, turns = create(c)
        old = turns[0]["id"]
        actions = {
            "send": (f"/v1/conversations/{conv['id']}/messages", {"prompt": "next"}),
            "edit": (f"/v1/runs/{old}/edit", {"prompt": "edited"}),
            "switch": (f"/v1/conversations/{conv['id']}/active", {"run_id": old}),
        }
        path, body = actions[operation]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: c.post(path, headers=AUTH, json=body), range(2)))
        assert sorted(r.status_code for r in responses) == ([200, 200] if operation == "switch" else [201, 409])
        with SessionLocal() as db:
            runs = list(db.scalars(select(Run).where(Run.conversation_id == conv["id"])))
            assert len(runs) == (1 if operation == "switch" else 2)
            assert sum(db.get(OutboxMessage, r.id) is not None for r in runs) == len(runs)


def test_version_cap_attachments_checks_privacy_and_depth():
    with TestClient(app) as c:
        conv, turns = create(c)
        old = turns[0]["id"]
        other = _other_user(c)
        for path, body in [(f"/v1/runs/{old}/edit", {"prompt": "x"}), (f"/v1/conversations/{conv['id']}/active", {"run_id": old})]:
            assert c.post(path, headers=other, json=body).status_code == 404
        assert (
            c.post(f"/v1/runs/{old}/edit", headers=AUTH, json={"prompt": "x", "file_ids": ["missing"]}).json()["detail"] == "附件已不可用"
        )
        assert c.post(f"/v1/runs/{old}/edit", headers=AUTH, json={"prompt": " "}).status_code == 422
        with SessionLocal() as db:
            owner = db.get(Run, old).user_id
            db.add_all([Run(user_id=owner, conversation_id=conv["id"], prompt="sibling", status="succeeded") for _ in range(49)])
            db.commit()
        assert c.post(f"/v1/runs/{old}/retry", headers=AUTH, json={}).status_code == 422
        assert c.post(f"/v1/runs/{old}/edit", headers=AUTH, json={"prompt": "x"}).status_code == 422
        with SessionLocal() as db:
            prior = old
            for index in range(499):
                node = Run(
                    id=f"depth-{old}-{index}",
                    parent_run_id=prior,
                    user_id=owner,
                    conversation_id=conv["id"],
                    prompt="depth",
                    status="succeeded",
                )
                db.add(node)
                prior = node.id
            db.get(Conversation, conv["id"]).active_run_id = prior
            db.commit()
        assert c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "too deep"}).status_code == 422


def test_pin_order_and_context_meter_uses_only_settled_main_active_leaf():
    from ailab_platform.models import PlatformSetting

    with TestClient(app) as c:
        conv, turns = create(c)
        root = turns[0]["id"]
        assert detail(c, conv)["context"]["used_tokens"] is None
        with SessionLocal() as db:
            for quiet, status, tokens in [(None, "settled", 10), (False, "settled", 20), (True, "settled", 30), (False, "estimated", 40)]:
                db.add(ModelUsage(run_id=root, quiet=quiet, status=status, alias="test-fast", prompt_tokens=tokens))
            setting = db.get(PlatformSetting, "context")
            if setting:
                setting.value = {"auto_compact": True, "threshold_ratio": 0.5}
            else:
                db.add(PlatformSetting(key="context", value={"auto_compact": True, "threshold_ratio": 0.5}))
            db.commit()
        ctx = detail(c, conv)["context"]
        spec = next(m for m in c.get("/v1/models", headers=AUTH).json()["models"] if m["id"] == "test-fast")
        assert ctx == {"used_tokens": 20, "model": "test-fast", "compact_at_tokens": spec["context_tokens"] // 2}
        assert spec["input_budget_tokens"] <= spec["context_tokens"]
        create(c)
        pinned = c.patch(f"/v1/conversations/{conv['id']}", headers=AUTH, json={"pinned": True})
        assert pinned.status_code == 200 and pinned.json()["pinned_at"]
        assert c.get("/v1/conversations", headers=AUTH).json()[0]["id"] == conv["id"]
        assert c.patch(f"/v1/conversations/{conv['id']}", headers=AUTH, json={"pinned": False}).json()["pinned_at"] is None
        assert c.patch(f"/v1/conversations/{conv['id']}", headers=AUTH, json={"pinned": None}).status_code == 422
        new = c.post(f"/v1/runs/{root}/retry", headers=AUTH, json={}).json()
        assert detail(c, conv)["context"]["used_tokens"] is None
        _finish(c, new["id"], "ok")
        assert c.post(f"/v1/conversations/{conv['id']}/active", headers=AUTH, json={"run_id": root}).json()["context"]["used_tokens"] == 20


def test_branch_files_are_not_implicit_attachments_of_the_new_path():
    from fastapi import HTTPException

    from ailab_platform.api.routes_internal import _bound_file
    from ailab_platform.models import Artifact, FileObject
    from ailab_platform.service_auth import SERVICE_SCOPES, ServicePrincipal

    with TestClient(app) as c:
        _conv, turns = create(c, ("chat", "chat"))
        old = turns[-1]["id"]
        with SessionLocal() as db:
            owner = db.get(Run, old).user_id
            file = FileObject(user_id=owner, filename="old.txt", status="ready", storage_key="unused")
            db.add(file)
            db.flush()
            db.add(Artifact(run_id=old, file_id=file.id))
            file_id = file.id
            db.commit()
        edited = c.post(f"/v1/runs/{old}/edit", headers=AUTH, json={"prompt": "new path"}).json()
        with SessionLocal() as db:
            principal = ServicePrincipal("broker", SERVICE_SCOPES["broker"])
            with pytest.raises(HTTPException) as exc:
                _bound_file(db, file_id, principal, edited["id"], "run")
            assert exc.value.status_code == 404
            db.get(Run, edited["id"]).input_file_ids = [file_id]
            db.commit()
            assert _bound_file(db, file_id, principal, edited["id"], "run").id == file_id


def test_quiet_identity_is_frozen_in_real_call_reservation_and_ledger():
    from uuid import uuid4

    from ailab_platform.contracts.usage import ReserveUsageIn, SettleUsageIn
    from ailab_platform.quota import reserve_usage, settle_usage

    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "meter", "mode": "chat"}).json()
        attempt = attempt_headers(c, run["id"])["X-Run-Attempt"]
        with SessionLocal() as db:
            for quiet, tokens in [(False, 321), (True, 123)]:
                call_id = uuid4().hex
                reservation = ReserveUsageIn(
                    call_id=call_id,
                    run_id=run["id"],
                    attempt_id=attempt,
                    alias=run["model"],
                    mode="chat",
                    quiet=quiet,
                    estimated_input_tokens=100,
                    max_output_tokens=100,
                )
                reserve_usage(db, reservation)
                settle_usage(db, SettleUsageIn(call_id=call_id, attempt_id=attempt, input_tokens=tokens, output_tokens=1, status="settled"))
                assert db.scalar(select(ModelUsage.quiet).where(ModelUsage.call_id == call_id)) is quiet
        assert detail(c, conv)["context"]["used_tokens"] == 321


@pytest.mark.parametrize("mutation", ["messages", "edit"])
def test_switch_racing_a_new_run_keeps_one_active_path(mutation):
    with TestClient(app) as c:
        conv, turns = create(c)
        root = turns[0]["id"]
        active_url = f"/v1/conversations/{conv['id']}/active"
        mutation_url = f"/v1/conversations/{conv['id']}/messages" if mutation == "messages" else f"/v1/runs/{root}/edit"
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(c.post, active_url, headers=AUTH, json={"run_id": root}),
                pool.submit(c.post, mutation_url, headers=AUTH, json={"prompt": "new question"}),
            ]
            switch, created = [f.result() for f in futures]
        assert switch.status_code in (200, 409)
        assert created.status_code == 201
        d = detail(c, conv)
        assert d["active_run_id"] == created.json()["id"] and d["last_status"] == "queued"
        assert [r["id"] for r in d["turns"]] == ([root, created.json()["id"]] if mutation == "messages" else [created.json()["id"]])
