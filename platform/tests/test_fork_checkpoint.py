"""A same-mode fork continues from its own copy of the forked turn's exact model context.

Covers what is copied, when the fork falls back to a rebuild, and that the copy is owned by the
branch for its whole lifecycle: failed forks, purged branches, purged sources, expiry after a
child, orphan sweeps and file-source invalidation.
"""

import asyncio
import gzip
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.context_rendering import parse_summary
from ailab_platform.agent.tools import ToolContext, execute_tool
from ailab_platform.contracts.context_summary import ContextSummary, canonical_summary
from ailab_platform.db import SessionLocal
from ailab_platform.llm import LLMResponse
from ailab_platform.models import Conversation, LifecycleDeletion, Run, RunCheckpoint
from ailab_platform.storage import get_storage
from tests.test_ledger_protocol import AUTH, SERVICE, api  # noqa: F401
from tests.test_runtime_protocol import save
from tests.usage_helpers import owned_headers

PLATFORM = {"role": "system", "content": "platform"}
BOUNDARY = {"role": "system", "content": "boundary"}


def finished(messages, **extra):
    return {**fresh_state(), "finished": True, "tool_catalog_v1": [{"type": "function", "function": {"name": "t"}}], **extra} | {
        "messages": messages
    }


def turn(api, conv, prompt, state, *, mode="work", status="succeeded"):  # noqa: F811
    response = api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": prompt, "mode": mode})
    assert response.status_code == 201, response.text
    rid = response.json()["id"]
    headers = owned_headers(api, rid)
    assert api.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "running"}).status_code == 200
    if state is not None:
        assert save(api, rid, headers, state=state).status_code == 200
    assert api.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": status}).status_code == 200
    return rid


def conversation(api):  # noqa: F811
    return api.post("/v1/conversations", headers=AUTH, json={}).json()["id"]


def fork(api, conv, run_id, mode="work"):  # noqa: F811
    response = api.post(f"/v1/conversations/{conv}/fork", headers=AUTH, json={"run_id": run_id, "mode": mode})
    assert response.status_code == 201, response.text
    branch = response.json()["id"]
    with SessionLocal() as db:
        leaf = db.get(Conversation, branch).active_run_id
    return branch, leaf


def prior(api, conv, prompt="next", mode="work", model=None):  # noqa: F811
    body = {"prompt": prompt, "mode": mode, **({"model": model} if model else {})}
    rid = api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json=body).json()["id"]
    return rid, api.get(f"/internal/runs/{rid}/model-input", headers=SERVICE).json()


def checkpoint_keys(run_id):
    return [item.key for item in get_storage().list_prefix(f"checkpoints/{run_id}/").items]


def stored_state(run_id):
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        row = db.get(RunCheckpoint, (run_id, run.checkpoint_step))
        return row, json.loads(gzip.decompress(get_storage().get_bytes(row.state_key)))


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_same_mode_fork_inherits_exact_bytes_as_its_own_snapshot(api, mode):  # noqa: F811
    conv = conversation(api)
    messages = [
        PLATFORM,
        BOUNDARY,
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": ""}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "tool output kept"},
        {"role": "assistant", "content": "first answer"},
    ]
    source = turn(
        api,
        conv,
        "first",
        finished(
            messages,
            context_epoch=4,
            memory_observed={"a": 1},
            memory_snapshot_signature="sig",
            prompt_anchors=[{"end": 2, "digest": "d", "tokens": 9}],
            plan=[{"step": "done"}],
            delivered={"/workspace/outputs/a.txt": "art"},
            delivery_names={"/workspace/outputs/a.txt": "a.txt"},
            workspace_dirty=True,
            pending=[{"id": "p"}],
            file_wait_deadlines={"f": 1},
            background_jobs=["job"],
            loaded_skills=[{"name": "x"}],
        ),
        mode=mode,
    )
    branch, leaf = fork(api, conv, source, mode)
    row, copied = stored_state(leaf)
    assert row.expires_at is None and row.state_key.startswith(f"checkpoints/{leaf}/")
    # Run-bound execution state stays with the source run.
    assert copied["background_jobs"] == [] and "loaded_skills" not in copied and "tool_log" not in copied
    _, model_input = prior(api, branch, mode=mode)
    inherited = model_input["previous_model_context"]
    assert inherited["messages"] == messages and inherited["context_epoch"] == 4
    assert inherited["memory_observed"] == {"a": 1}
    assert inherited["memory_snapshot_signature"] == "sig"
    assert inherited["prompt_anchors"] == [{"end": 2, "digest": "d", "tokens": 9}]
    assert inherited["delivered"] == {"/workspace/outputs/a.txt": "art"}
    assert inherited["delivery_names"] == {"/workspace/outputs/a.txt": "a.txt"}
    operational = inherited["operational_state"]
    assert operational["plan"] == [{"step": "done"}] and operational["workspace_dirty"] is True
    assert operational["background_jobs"] == [] and operational["pending"] == [] and operational["file_wait_deadlines"] == {}
    assert operational["workspace"] is None  # no workspace was cloned for this branch
    # The source keeps its own snapshot untouched.
    assert stored_state(source)[1]["background_jobs"] == ["job"]


def test_cross_mode_fork_rebuilds(api):  # noqa: F811
    conv = conversation(api)
    source = turn(api, conv, "chat", finished([PLATFORM, BOUNDARY, {"role": "user", "content": "chat"}]), mode="chat")
    branch, leaf = fork(api, conv, source, "work")
    assert checkpoint_keys(leaf) == []
    with SessionLocal() as db:
        assert db.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == leaf)) is None
        assert not (db.get(Run, leaf).runtime_meta or {}).get("context_snapshot")
    _, model_input = prior(api, branch)
    assert "previous_model_context" not in model_input


def test_fork_before_a_later_compaction_uses_that_turns_context(api):  # noqa: F811
    conv = conversation(api)
    early = [PLATFORM, BOUNDARY, {"role": "user", "content": "early"}, {"role": "assistant", "content": "early answer"}]
    first = turn(api, conv, "early", finished(early, context_epoch=2))
    compacted = [PLATFORM, BOUNDARY, {"role": "system", "content": "<conversation_checkpoint>summary</conversation_checkpoint>"}]
    second = turn(api, conv, "late", finished(compacted, context_epoch=3))
    branch, _ = fork(api, conv, first)
    inherited = prior(api, branch)[1]["previous_model_context"]
    assert inherited["messages"] == early and inherited["context_epoch"] == 2
    later, _ = fork(api, conv, second)
    inherited = prior(api, later)[1]["previous_model_context"]
    assert inherited["messages"] == compacted and inherited["context_epoch"] == 3


@pytest.mark.parametrize("case", ["no_checkpoint", "expired_checkpoint", "failed_turn", "unfinished_state"])
def test_fork_without_a_usable_snapshot_rebuilds(api, case):  # noqa: F811
    conv = conversation(api)
    state = finished([PLATFORM, BOUNDARY, {"role": "user", "content": "x"}])
    if case == "no_checkpoint":
        state = None
    if case == "unfinished_state":
        state = {**state, "finished": False}
    source = turn(api, conv, "x", state, status="failed" if case == "failed_turn" else "succeeded")
    if case == "expired_checkpoint":
        with SessionLocal() as db:
            for row in db.scalars(select(RunCheckpoint).where(RunCheckpoint.run_id == source)):
                db.delete(row)
            db.commit()
    branch, leaf = fork(api, conv, source)
    assert checkpoint_keys(leaf) == []
    assert "previous_model_context" not in prior(api, branch)[1]


@pytest.mark.parametrize("case", ["oversized", "storage_down"])
def test_snapshot_copy_failure_still_forks_and_rebuilds(api, monkeypatch, case):  # noqa: F811
    from ailab_platform.config import settings

    conv = conversation(api)
    source = turn(api, conv, "x", finished([PLATFORM, BOUNDARY, {"role": "user", "content": "x" * 4000}]))
    if case == "oversized":
        monkeypatch.setattr(settings, "checkpoint_max_bytes", 2048)
    else:
        storage = get_storage()
        original = storage.put_bytes

        def refuse(key, *args, **kwargs):
            if key.startswith("checkpoints/"):
                raise OSError("storage offline")
            return original(key, *args, **kwargs)

        monkeypatch.setattr(storage, "put_bytes", refuse)
    branch, leaf = fork(api, conv, source)
    monkeypatch.undo()
    assert checkpoint_keys(leaf) == []
    with SessionLocal() as db:
        assert db.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == leaf)) is None
    assert "previous_model_context" not in prior(api, branch)[1]


def test_work_fork_with_a_saved_workspace_reports_it_available(api):  # noqa: F811
    conv = conversation(api)
    response = api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "build", "mode": "work"})
    source = response.json()["id"]
    headers = owned_headers(api, source)
    assert api.post(f"/internal/runs/{source}/transition", headers=headers, json={"status": "running"}).status_code == 200
    key = f"workspaces/{source}/snap.tgz"
    get_storage().put_bytes(key, b"workspace")
    workspace = {"key": key, "saved_at": datetime.now(UTC).isoformat()}
    state = finished([PLATFORM, BOUNDARY, {"role": "user", "content": "build"}], workspace=workspace)
    assert save(api, source, headers, state=state).status_code == 200
    assert api.post(f"/internal/runs/{source}/transition", headers=headers, json={"status": "succeeded"}).status_code == 200
    branch, leaf = fork(api, conv, source)
    assert stored_state(leaf)[1]["workspace"] == {"available": True}
    model_input = prior(api, branch)[1]
    assert model_input["previous_model_context"]["operational_state"]["workspace"] == {"available": True}
    assert model_input["workspace_restore_key"] and model_input["workspace_restore_key"] != key


def test_a_model_change_on_the_branch_is_a_recorded_boundary(api):  # noqa: F811
    conv = conversation(api)
    source = turn(api, conv, "x", finished([PLATFORM, BOUNDARY, {"role": "user", "content": "x"}]))
    branch, _ = fork(api, conv, source)
    model_input = prior(api, branch, model="test-fast")[1]
    assert model_input["context_boundary"] == "model_changed" and "previous_model_context" not in model_input


def test_structured_run_handles_move_and_text_handles_become_aliases(api):  # noqa: F811
    conv = conversation(api)
    first = turn(api, conv, "one", finished([PLATFORM, BOUNDARY, {"role": "user", "content": "one"}]))
    text = [PLATFORM, BOUNDARY, {"role": "system", "content": json.dumps({"sources": [{"run_id": first}]})}]
    second = turn(api, conv, "two", finished(text, context_sources=[{"run_id": first}, {"file_id": "file_x"}]))
    branch, leaf = fork(api, conv, second)
    with SessionLocal() as db:
        copy_of_first = db.get(Run, leaf).parent_run_id
    _, copied = stored_state(leaf)
    assert copied["messages"] == text  # never rewritten: the prefix must stay byte-identical
    assert copied["context_sources"] == [{"run_id": copy_of_first}, {"file_id": "file_x"}]
    assert copied["run_aliases"] == {first: copy_of_first}
    assert prior(api, branch)[1]["previous_model_context"]["run_aliases"] == {first: copy_of_first}
    # A fork of the fork composes: the original handle resolves to the newest copy.
    again, again_leaf = fork(api, branch, leaf)
    with SessionLocal() as db:
        newest_first = db.get(Run, again_leaf).parent_run_id
    assert stored_state(again_leaf)[1]["run_aliases"] == {first: newest_first}
    assert prior(api, again)[1]["previous_model_context"]["messages"] == text


def test_branches_share_their_lineage_root_provider_session(api, monkeypatch):  # noqa: F811
    conv = conversation(api)
    source = turn(api, conv, "x", finished([PLATFORM, BOUNDARY, {"role": "user", "content": "x"}]))
    assert prior(api, conv)[1]["cache_session"] == conv
    branch, leaf = fork(api, conv, source)
    nested, _ = fork(api, branch, leaf)
    assert prior(api, branch)[1]["cache_session"] == conv
    rid, model_input = prior(api, nested)
    assert model_input["cache_session"] == conv
    purge(api, conv, monkeypatch)  # a purged root still names the lineage
    assert api.get(f"/internal/runs/{rid}/model-input", headers=SERVICE).json()["cache_session"] == conv


def test_text_aliases_resolve_summary_sources_and_recall():
    allowed = {"run_id": {"run_new"}, "file_id": set(), "block_id": set()}
    summary = ContextSummary.model_validate({"objective": "x", "facts": [{"id": "f1", "text": "f", "sources": [{"run_id": "run_old"}]}]})
    response = LLMResponse(content=canonical_summary(summary))
    with pytest.raises(ValueError):
        parse_summary(response, allowed)
    parsed = parse_summary(response, allowed, {"run_old": "run_new"})
    assert [source.run_id for source in parsed.sources()] == ["run_new"]

    class Platform:
        async def recall(self, run_id, payload):
            self.payload = payload
            return {"run_id": payload["run_id"]}

    # Inherited recall results are revalidated against the branch copy, not the source run.
    from ailab_platform.agent.loop import AgentLoop
    from tests.test_agent_loop import FakePlatform

    class Recall(FakePlatform):
        def __init__(self):
            super().__init__()
            self.checked = []

        async def recall(self, run_id, payload):
            self.checked.append(payload["run_id"])
            return {"run_id": payload["run_id"]}

    recall = Recall()
    loop = AgentLoop(platform=recall)
    loop._states["r"] = {**fresh_state(), "run_aliases": {"run_old": "run_new"}}
    inherited = [{"role": "tool", "name": "recall_history", "content": json.dumps({"run_id": "run_old"})}]
    assert asyncio.run(loop._revoked_recall_results("r", inherited)) == []
    assert recall.checked == ["run_new"]

    platform = Platform()
    ctx = ToolContext("run_current", platform)
    ctx.run_aliases = {"run_old": "run_new"}
    asyncio.run(execute_tool("recall_history", {"run_id": "run_old"}, "", None, ctx))
    assert platform.payload["run_id"] == "run_new"


def test_failed_fork_removes_its_copied_snapshot(api, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from ailab_platform.api import fork_context

    conv = conversation(api)
    source = turn(api, conv, "x", finished([PLATFORM, BOUNDARY, {"role": "user", "content": "x"}]))
    original = fork_context.store_fork_checkpoint
    written = []

    def fail_after_write(db, leaf, state, put):
        original(db, leaf, state, put)
        written.append(leaf.id)
        raise HTTPException(422, "injected")

    monkeypatch.setattr(fork_context, "store_fork_checkpoint", fail_after_write)
    with SessionLocal() as db:
        before = db.scalar(select(Conversation.id).order_by(Conversation.created_at.desc()).limit(1))
    response = api.post(f"/v1/conversations/{conv}/fork", headers=AUTH, json={"run_id": source, "mode": "work"})
    assert response.status_code == 422
    assert written and checkpoint_keys(written[0]) == []
    with SessionLocal() as db:
        assert db.get(Run, written[0]) is None
        assert db.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == written[0])) is None
        assert db.scalar(select(Conversation.id).order_by(Conversation.created_at.desc()).limit(1)) == before


def settle(db):
    from ailab_platform.api.reaper import drain_object_deletions

    for _ in range(50):
        if not drain_object_deletions(db, get_storage()):
            break


def purge(api, conv, monkeypatch):  # noqa: F811
    from ailab_platform.api.reaper import purge_deleted_conversations
    from ailab_platform.config import settings

    monkeypatch.setattr(settings, "gc_batch_size", 1000)
    assert api.delete(f"/v1/conversations/{conv}", headers=AUTH).status_code == 204
    with SessionLocal() as db:
        db.get(Conversation, conv).deleted_at = datetime.now(UTC) - timedelta(days=settings.conversation_purge_days + 1)
        db.commit()
        for _ in range(20):
            purge_deleted_conversations(db, datetime.now(UTC))
            if db.get(Conversation, conv) is None:
                break
        assert db.get(Conversation, conv) is None
        settle(db)


def test_purging_the_branch_removes_its_snapshot_and_the_source_survives(api, monkeypatch):  # noqa: F811
    conv = conversation(api)
    messages = [PLATFORM, BOUNDARY, {"role": "user", "content": "x"}]
    source = turn(api, conv, "x", finished(messages))
    branch, leaf = fork(api, conv, source)
    assert len(checkpoint_keys(leaf)) == 1
    purge(api, branch, monkeypatch)
    assert checkpoint_keys(leaf) == []
    with SessionLocal() as db:
        assert db.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == leaf)) is None
        assert db.scalar(select(LifecycleDeletion).where(LifecycleDeletion.key.like(f"checkpoints/{leaf}/%"))) is None
    assert len(checkpoint_keys(source)) == 1
    assert prior(api, conv)[1]["previous_model_context"]["messages"] == messages


def test_purging_the_source_keeps_the_branch_snapshot(api, monkeypatch):  # noqa: F811
    conv = conversation(api)
    messages = [PLATFORM, BOUNDARY, {"role": "user", "content": "x"}]
    source = turn(api, conv, "x", finished(messages))
    branch, leaf = fork(api, conv, source)
    purge(api, conv, monkeypatch)
    assert checkpoint_keys(source) == []
    assert len(checkpoint_keys(leaf)) == 1
    assert prior(api, branch)[1]["previous_model_context"]["messages"] == messages


def test_branch_snapshot_ages_after_its_child_and_survives_orphan_sweeps(api):  # noqa: F811
    from ailab_platform.api.reaper import purge_checkpoints

    conv = conversation(api)
    source = turn(api, conv, "x", finished([PLATFORM, BOUNDARY, {"role": "user", "content": "x"}]))
    branch, leaf = fork(api, conv, source)
    (key,) = checkpoint_keys(leaf)
    # A fork that crashed between writing its copy and committing leaves an unreferenced object.
    orphan = f"checkpoints/{leaf}/1-crashed.json.gz"
    get_storage().put_bytes(orphan, gzip.compress(b"{}"), "application/gzip")
    far = datetime.now(UTC) + timedelta(days=60)
    with SessionLocal() as db:
        purge_checkpoints(db, get_storage(), far)  # a leaf never expires and its object is referenced
        settle(db)
    assert checkpoint_keys(leaf) == [key]
    turn(api, branch, "child", finished([PLATFORM, BOUNDARY, {"role": "user", "content": "child"}]))
    with SessionLocal() as db:
        row = db.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == leaf))
        expires = row.expires_at.replace(tzinfo=row.expires_at.tzinfo or UTC)
        assert timedelta(days=6) < expires - datetime.now(UTC) <= timedelta(days=7)
        purge_checkpoints(db, get_storage(), datetime.now(UTC) + timedelta(days=8))
        settle(db)
        assert db.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == leaf)) is None
    assert checkpoint_keys(leaf) == []


def test_invalidated_file_source_removes_the_branch_snapshot(api):  # noqa: F811
    from ailab_platform.api.source_lifecycle import drain_invalidations
    from tests.test_document_context import upload

    fid = upload(api, "fork-source.txt", b"PRIVATE", "text/plain")
    conv = conversation(api)
    response = api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "read", "file_ids": [fid]})
    source = response.json()["id"]
    headers = owned_headers(api, source)
    assert api.post(f"/internal/runs/{source}/transition", headers=headers, json={"status": "running"}).status_code == 200
    state = finished([PLATFORM, BOUNDARY, {"role": "user", "content": "read"}, {"role": "assistant", "content": "PRIVATE"}])
    assert save(api, source, headers, state=state).status_code == 200
    assert api.post(f"/internal/runs/{source}/transition", headers=headers, json={"status": "succeeded"}).status_code == 200
    branch, leaf = fork(api, conv, source)
    assert len(checkpoint_keys(leaf)) == 1
    assert api.delete(f"/v1/files/{fid}", headers=AUTH).status_code == 204
    with SessionLocal() as db:
        for _ in range(50):
            if not drain_invalidations(db):
                break
        settle(db)
        assert db.scalar(select(RunCheckpoint).where(RunCheckpoint.run_id == leaf)) is None
    assert checkpoint_keys(leaf) == [] and checkpoint_keys(source) == []
    model_input = prior(api, branch)[1]
    assert "previous_model_context" not in model_input and "PRIVATE" not in json.dumps(model_input)


@pytest.mark.parametrize("copyable", [True, False])
def test_capture_references_move_to_the_branch_copies(api, copyable):  # noqa: F811
    from ailab_platform.models import ViewerCapture
    from tests.run_claims import attempt_headers
    from tests.test_document_view import capture_setup

    rid, cid, _fid, _version, token, value = capture_setup(api)
    if copyable:  # catalog v2 keeps captures with their message; v1 captures are 10-minute temporaries
        with SessionLocal() as db:
            run = db.get(Run, rid)
            run.options = {**run.options, "document_tools_version": 2}
            db.commit()
    result = api.post(f"/v1/runs/{rid}/client-calls/capture/result", headers=AUTH, json={"claim_token": token, "ok": True, "value": value})
    assert result.status_code == 200, result.text
    capture = api.get(f"/internal/runs/{rid}/client-calls/capture", headers=SERVICE).json()["result"]["value"]["capture_id"]
    original = api.get(f"/internal/runs/{rid}/captures/{capture}", headers=SERVICE).content
    image = {
        "type": "ailab_image",
        "file": {"capture_id": capture, "filename": "文档截图", "page": 1, "content_type": "image/png"},
        "sha256": "0" * 64,
        "header": "data:image/png;base64",
        "detail": None,
    }
    messages = [PLATFORM, BOUNDARY, {"role": "user", "content": [{"type": "text", "text": "看图"}, image]}]
    headers = attempt_headers(api, rid)
    assert save(api, rid, headers, state=finished(messages)).status_code == 200
    assert api.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "succeeded"}).status_code == 200
    with SessionLocal() as db:
        assert (db.get(ViewerCapture, capture).expires_at is None) == copyable
    branch, leaf = fork(api, cid, rid)
    if not copyable:
        assert checkpoint_keys(leaf) == []
        return
    copied = stored_state(leaf)[1]["messages"][2]["content"][1]
    moved = copied["file"]["capture_id"]
    assert moved != capture and {**copied, "file": {**copied["file"], "capture_id": capture}} == image
    with SessionLocal() as db:
        model = db.get(Run, rid).model  # the capture fixture's run uses its own model
    child, model_input = prior(api, branch, model=model)
    assert model_input["previous_model_context"]["messages"][2]["content"][1]["file"]["capture_id"] == moved
    inherited = api.get(f"/internal/runs/{child}/captures/{moved}", headers=SERVICE)
    assert inherited.status_code == 200 and inherited.content == original
