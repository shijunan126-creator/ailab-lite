"""Run-scoped contexts and durable concurrency boundaries (HLD 71/72)."""

import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.loop import AgentLoop, _Turn
from ailab_platform.agent.tools import tool_result_message
from ailab_platform.contracts.context_summary import ContextSummary, canonical_summary
from ailab_platform.contracts.model_context import tool_catalog_fingerprint
from ailab_platform.db import SessionLocal
from ailab_platform.model_transport import ModelTransport
from ailab_platform.models import ContextSummaryFrame, Conversation, Run
from tests.prefix_harness import scripted_server
from tests.test_agent_loop import FakeBroker, FakePlatform
from tests.test_ledger_protocol import AUTH, SERVICE, api  # noqa: F401
from tests.test_runtime_protocol import owned, save
from tests.usage_helpers import owned_headers


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def test_concurrent_users_share_only_platform_prefix_bytes():
    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {
                **await super().get_run(run_id),
                "user_id": run_id,
                "model": "test-fast",
                "conversation_id": "conversation-" + run_id,
                "prompt": "prompt-" + run_id,
                "memory": {"enabled": True, "entries": [{"id": run_id, "kind": "user", "content": "memory-" + run_id}]},
                "options": {"mode": "chat"},
            }

        async def get_model_context(self, run_id, **kwargs):
            await asyncio.sleep(0)  # both runs have bootstrapped in the same AgentLoop
            return {
                "summary": canonical_summary(ContextSummary(objective="checkpoint-" + run_id)),
                "through_run_id": "ancestor-" + run_id,
                "history": [{"id": "history-" + run_id, "prompt": "history-" + run_id, "reply": "reply-" + run_id}],
                "has_more": False,
            }

        async def put_checkpoint(self, run_id, payload):
            self.saved[run_id] = copy.deepcopy(payload["state"])
            return {"step": payload["expected_step"] + 1}

    async def exercise(loop):
        await asyncio.gather(loop.process("tenant-A", attempt_id="a"), loop.process("tenant-B", attempt_id="b"))

    with scripted_server([{"content": "done"}, {"content": "done"}]) as (url, requests):
        platform = Platform()
        platform.saved = {}
        asyncio.run(exercise(AgentLoop(platform, FakeBroker(), ModelTransport(url))))
    assert len(requests) == 2
    a, b = sorted(requests, key=lambda r: r["messages"][-1]["content"])
    assert wire(a["messages"][0]) == wire(b["messages"][0])
    assert wire(a["tools"]) == wire(b["tools"])
    assert wire(a["messages"][1]) != wire(b["messages"][1])
    for request, owner, other in [(a, "tenant-A", "tenant-B"), (b, "tenant-B", "tenant-A")]:
        payload = wire(request).decode()
        assert all(prefix + owner in payload for prefix in ["memory-", "checkpoint-", "history-", "prompt-"])
        assert other not in payload
        assert other not in wire(platform.saved[owner]).decode()
    assert platform.saved["tenant-A"]["tool_catalog_fingerprint"] == platform.saved["tenant-B"]["tool_catalog_fingerprint"]


def test_catalog_fingerprint_distinguishes_permissions_with_identical_tool_names():
    files = [{"function": {"name": "read", "parameters": {"enum": ["files"]}}, "type": "function"}]
    github = [{"type": "function", "function": {"parameters": {"enum": ["github"]}, "name": "read"}}]
    reordered_keys = [{"type": "function", "function": {"parameters": {"enum": ["files"]}, "name": "read"}}]
    assert tool_catalog_fingerprint(files) == tool_catalog_fingerprint(reordered_keys)
    assert tool_catalog_fingerprint(files) != tool_catalog_fingerprint(github)
    assert tool_catalog_fingerprint(files) != tool_catalog_fingerprint([*files, *github])
    loop = AgentLoop(FakePlatform())
    prefix = [{"role": "system", "content": "platform"}, {"role": "system", "content": "boundary"}]
    for run_id, catalog in [("files", files), ("github", github)]:
        loop._states[run_id] = {**fresh_state(), "messages": copy.deepcopy(prefix), "tool_catalog_v1": catalog}
        turn = _Turn({"options": {"mode": "work"}})
        asyncio.run(loop._context(run_id).build(turn.run, turn, "m", loop.llm))
        saved = loop._context(run_id).snapshot({})
        assert saved["tool_catalog_fingerprint"] == tool_catalog_fingerprint(catalog)
        assert saved["messages"] == prefix and saved["tool_catalog_v1"] == catalog
    assert loop._states["files"]["tool_catalog_fingerprint"] != loop._states["github"]["tool_catalog_fingerprint"]
    # Execution authorization still uses the offered catalog, never this digest.
    assert files[0]["function"]["parameters"]["enum"] == ["files"]


def test_run_operations_and_same_name_personal_skills_are_isolated():
    loop = AgentLoop(FakePlatform())
    name = "tenant-skill"
    call = {"id": "load", "type": "function", "function": {"name": "load_skill", "arguments": json.dumps({"name": name})}}
    a, b = _Turn({"options": {"mode": "work"}}), _Turn({"options": {"mode": "work"}})
    for turn, fid in [(a, "file-A"), (b, "file-B")]:
        turn.personal = {name: {"name": name, "file_id": fid, "filename": "skill.zip"}}
    a.messages = [
        {"role": "assistant", "content": "", "tool_calls": [call]},
        tool_result_message("load", "load_skill", {"ok": True, "skill": "private-A", "file_id": "file-A", "mode": "work"}),
    ]
    b.messages = copy.deepcopy(a.messages)  # even an identically named carried result cannot authorize B's version
    loop._states.update(A={**fresh_state(), "messages": a.messages}, B={**fresh_state(), "messages": b.messages})
    loop._register_carried_skills(a, "A")
    loop._register_carried_skills(b, "B")
    assert [s["name"] for s in loop._states["A"]["loaded_skills"]] == [name]
    assert loop._states["B"]["loaded_skills"] == []
    loop._states["B"]["messages"] = []
    ca, cb = loop._context("A"), loop._context("B")
    cb.state["background_jobs"] = ["B-job"]
    ca.append({"role": "user", "content": "only A"})
    ca.state["context_epoch"] = 9
    assert cb.snapshot({})["messages"] == []
    assert cb.state["context_epoch"] == 0 and cb.state["background_jobs"] == ["B-job"]


def test_checkpoint_writers_reject_stale_compaction_and_epoch(api):  # noqa: F811
    rid, _, headers = owned(api)
    initial = {**fresh_state(), "context_epoch": 3, "messages": [{"role": "user", "content": "original"}]}
    assert save(api, rid, headers, state=initial).status_code == 200
    barrier = Barrier(2)

    def write(label):
        candidate = {
            **copy.deepcopy(initial),
            "context_epoch": 4,
            "messages": [{"role": "user", "content": label}],
            "context_boundary": {"reason": "compacted", "epoch": 4, "run_id": rid},
        }
        barrier.wait(timeout=10)
        return label, save(api, rid, headers, step=1, state=candidate)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(write, ["compaction-A", "compaction-B"]))
    assert sorted(response.status_code for _, response in results) == [200, 409]
    loser = next(response for _, response in results if response.status_code == 409)
    assert loser.json()["detail"] == "stale_step"
    winner = next(label for label, response in results if response.status_code == 200)
    saved = api.get(f"/internal/runs/{rid}/checkpoint", headers=SERVICE).json()
    assert saved["step"] == 2 and saved["state"]["context_epoch"] == 4
    assert saved["state"]["messages"][-1]["content"] == winner


def conversation_candidate(api):  # noqa: F811
    conv = api.post("/v1/conversations", headers=AUTH, json={}).json()["id"]
    current = api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "current"}).json()["id"]
    with SessionLocal() as db:
        run = db.get(Run, current)
        parent = Run(user_id=run.user_id, conversation_id=conv, status="succeeded", prompt="ancestor", model=run.model)
        db.add(parent)
        db.flush()
        run.parent_run_id = parent.id
        db.commit()
        parent_id = parent.id
    headers = owned_headers(api, current)
    assert api.post(f"/internal/runs/{current}/transition", headers=headers, json={"status": "running"}).status_code == 200
    page = api.get(f"/internal/runs/{current}/model-context", headers=SERVICE).json()
    return (
        conv,
        current,
        headers,
        {
            "expected_through_run_id": None,
            "through_run_id": parent_id,
            "source_epoch": page["source_epoch"],
        },
    )


def test_conversation_summary_candidates_have_one_cas_winner(api):  # noqa: F811
    conv, rid, headers, body = conversation_candidate(api)
    barrier = Barrier(2)

    def write(label):
        barrier.wait(timeout=10)
        return label, api.put(
            f"/internal/runs/{rid}/model-summary",
            headers=headers,
            json={**body, "summary": canonical_summary(ContextSummary(objective=label))},
        )

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(write, ["summary-A", "summary-B"]))
    assert sorted(response.status_code for _, response in results) == [200, 422]
    winner = next(label for label, response in results if response.status_code == 200)
    with SessionLocal() as db:
        frames = db.scalars(select(ContextSummaryFrame).where(ContextSummaryFrame.conversation_id == conv)).all()
        assert len(frames) == 1 and frames[0].revision == 1
        assert ContextSummary.model_validate_json(db.get(Conversation, conv).summary).objective == winner


def test_fork_siblings_share_ancestor_bytes_but_advance_epochs_independently(api):  # noqa: F811
    conv = api.post("/v1/conversations", headers=AUTH, json={}).json()["id"]
    parent = api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "parent"}).json()["id"]
    headers = owned_headers(api, parent)
    assert api.post(f"/internal/runs/{parent}/transition", headers=headers, json={"status": "running"}).status_code == 200
    ancestor = [
        {"role": "system", "content": "platform"},
        {"role": "system", "content": "boundary"},
        {"role": "user", "content": "parent"},
        {"role": "assistant", "content": "answer"},
    ]
    state = {**fresh_state(), "finished": True, "messages": ancestor, "context_epoch": 7, "tool_catalog_v1": []}
    assert save(api, parent, headers, state=state).status_code == 200
    assert api.post(f"/internal/runs/{parent}/transition", headers=headers, json={"status": "succeeded"}).status_code == 200
    children = []
    first = None
    for label in ["A", "B"]:
        response = (
            api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": label})
            if first is None
            else api.post(f"/v1/runs/{first}/retry", headers=AUTH, json={})
        )
        assert response.status_code == 201, response.text
        rid = response.json()["id"]
        prior = api.get(f"/internal/runs/{rid}/model-input", headers=SERVICE).json()["previous_model_context"]
        assert wire(prior["messages"]) == wire(ancestor) and prior["context_epoch"] == 7
        owner = owned_headers(api, rid)
        assert api.post(f"/internal/runs/{rid}/transition", headers=owner, json={"status": "running"}).status_code == 200
        child_state = {**fresh_state(), "messages": [*prior["messages"], {"role": "user", "content": label}], "context_epoch": 7}
        assert save(api, rid, owner, state=child_state).status_code == 200
        if first is None:
            first = rid
            child_state.update(
                context_epoch=8,
                messages=[{"role": "system", "content": "A-compacted"}],
                context_boundary={"reason": "compacted", "epoch": 8, "run_id": rid},
            )
            assert save(api, rid, owner, step=1, state=child_state).status_code == 200
            assert api.post(f"/internal/runs/{rid}/transition", headers=owner, json={"status": "succeeded"}).status_code == 200
        children.append((rid, child_state))
    sibling = api.get(f"/internal/runs/{children[1][0]}/checkpoint", headers=SERVICE).json()["state"]
    assert sibling["context_epoch"] == 7 and sibling["messages"][:-1] == ancestor
    assert api.get(f"/internal/runs/{first}/checkpoint", headers=SERVICE).json()["state"]["context_epoch"] == 8
    assert api.get(f"/internal/runs/{parent}/checkpoint", headers=SERVICE).json()["state"] == state
    # A same-mode copy-conversation continues from its own copy of the forked turn's exact
    # context: the ancestor bytes, never either sibling's later compaction.
    for label in ["copy-A", "copy-B"]:
        fork = api.post(f"/v1/conversations/{conv}/fork", headers=AUTH, json={"run_id": parent, "mode": "work"})
        assert fork.status_code == 201, fork.text
        rid = api.post(f"/v1/conversations/{fork.json()['id']}/messages", headers=AUTH, json={"prompt": label}).json()["id"]
        prior = api.get(f"/internal/runs/{rid}/model-input", headers=SERVICE).json()["previous_model_context"]
        assert wire(prior["messages"]) == wire(ancestor) and prior["context_epoch"] == 7
        assert "A-compacted" not in wire(prior).decode()


def test_rebuild_revalidates_deleted_file_before_reusing_checkpoint(api):  # noqa: F811
    from tests.test_document_context import upload

    fid = upload(api, "context-source.txt", b"PRIVATE FILE CONTENT", "text/plain")
    conv = api.post("/v1/conversations", headers=AUTH, json={}).json()["id"]
    parent = api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "read file", "file_ids": [fid]}).json()["id"]
    owner = owned_headers(api, parent)
    assert api.post(f"/internal/runs/{parent}/transition", headers=owner, json={"status": "running"}).status_code == 200
    state = {
        **fresh_state(),
        "finished": True,
        "tool_catalog_v1": [],
        "messages": [
            {"role": "system", "content": "platform"},
            {"role": "system", "content": "boundary"},
            {"role": "user", "content": "read file"},
            {"role": "assistant", "content": "PRIVATE FILE CONTENT"},
        ],
    }
    assert save(api, parent, owner, state=state).status_code == 200
    assert (
        api.post(
            f"/internal/runs/{parent}/transition", headers=owner, json={"status": "succeeded", "summary": "PRIVATE FILE CONTENT"}
        ).status_code
        == 200
    )
    assert api.delete(f"/v1/files/{fid}", headers=AUTH).status_code == 204
    response = api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "continue"})
    assert response.status_code == 201, response.text
    rid = response.json()["id"]
    bootstrap = api.get(f"/internal/runs/{rid}/model-input", headers=SERVICE).json()
    assert bootstrap["context_boundary"] == "access_revoked"
    assert "previous_model_context" not in bootstrap
    page = api.get(f"/internal/runs/{rid}/model-context", headers=SERVICE).json()
    assert "PRIVATE FILE CONTENT" not in wire(page).decode()
    assert page["history"][0]["reply"] == "（文件来源已失效，需重新读取）"
