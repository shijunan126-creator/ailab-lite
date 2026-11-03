"""Operation retries, content reuse, source slots and durable output revisions."""

import asyncio
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.loop import AgentLoop, _Turn
from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import Artifact, FileObject, MessageFileRef, OutboxMessage, Run, UploadReceipt
from ailab_platform.runtime_context import delivery_id_var
from ailab_platform.sandbox.file_listing import LIST_PROGRAM, parse_listing
from ailab_platform.storage import get_storage
from scripts.repair_file_identity import batch
from tests.run_claims import attempt_headers, owned_headers
from tests.test_agent_loop import FakePlatform, ScriptedLLM
from tests.test_conversations import _other_user


@pytest.fixture
def api():
    with TestClient(app) as client:
        client.headers.update(_other_user(client))
        yield client


def upload(api, content=b"same", *, op=None, name="report.txt", **params):
    if op:
        params["client_upload_id"] = op
    return api.post("/v1/files", params=params, files={"upload": (name, content, "text/plain")})


def ready(file_id):
    with SessionLocal() as db:
        db.get(FileObject, file_id).status = "ready"
        db.commit()


def running(api, **params):
    response = api.post("/v1/runs", json={"prompt": "make", **params})
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    response = api.post(f"/internal/runs/{run_id}/transition", headers=owned_headers(api, run_id), json={"status": "running"})
    assert response.status_code == 200, response.text
    return run_id


def commit(api, run_id, data, *, key="report.txt", op=None, filename="report.txt"):
    staging = f"staging/test/{uuid4().hex}"
    get_storage().put_bytes(staging, data)
    body = {
        "run_id": run_id,
        "staging_key": staging,
        "filename": filename,
        "output_key": key,
        "content_type": "text/plain",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    headers = {**attempt_headers(api, run_id), "X-Delivery-Id": f"{run_id}:{op or uuid4().hex}"}
    return api.post("/internal/artifacts", headers=headers, json=body)


def test_upload_retry_reuse_copy_versions_and_tombstone(api):
    first = upload(api, op="pick-1").json()
    retry = upload(api, op="pick-1").json()
    assert first["id"] == retry["id"]
    assert upload(api, b"changed", op="pick-1").status_code == 422
    ready(first["id"])
    reused = upload(api, op="pick-2").json()
    assert reused["id"] == first["id"] and reused["reused"]
    copy = upload(api, op="copy-1", disposition="copy").json()
    assert copy["id"] != first["id"] and copy["filename"] == "report (2).txt"
    changed = upload(api, b"changed", op="different").json()
    assert changed["id"] not in {copy["id"], first["id"]}
    version = upload(api, b"version", op="v2", parent_id=first["id"]).json()
    assert version["lineage_id"] == first["lineage_id"] and version["version"] == 2
    assert api.delete(f"/v1/files/{first['id']}").status_code == 204
    assert upload(api, op="pick-1").status_code == 410
    purged = api.post("/v1/files/batch", json={"action": "purge", "ids": [first["id"]]})
    assert purged.status_code == 200, purged.text
    with SessionLocal() as db:
        receipts = db.scalars(select(UploadReceipt).where(UploadReceipt.file_id == first["id"])).all()
        assert receipts and all(r.filename is None for r in receipts)
    assert upload(api, op="pick-1").status_code == 410


def test_same_operation_concurrent_creates_one_file_and_job(api):
    def send(_):
        client = TestClient(app, headers=dict(api.headers))
        return upload(client, op="concurrent")

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(send, range(3)))
    assert [r.status_code for r in results] == [201] * 3
    ids = {r.json()["id"] for r in results}
    assert len(ids) == 1
    with SessionLocal() as db:
        assert len(db.scalars(select(UploadReceipt).where(UploadReceipt.file_id.in_(ids))).all()) == 1
        jobs = db.scalars(select(OutboxMessage).where(OutboxMessage.payload["file_id"].as_string().in_(ids))).all()
        assert len([j for j in jobs if j.payload.get("kind") == "file"]) == 1


def test_reuse_scope_and_provenance(api):
    first = upload(api, op="original").json()
    ready(first["id"])
    project = api.post("/v1/projects", json={"name": "isolated"}).json()["id"]
    assert upload(api, op="project", project_id=project).json()["id"] != first["id"]
    assert upload(api, op="other-name", name="renamed.txt").json()["id"] != first["id"]
    assert upload(api, op="skill", purpose="skill").json()["id"] != first["id"]
    assert upload(api, op="reused").json()["id"] == first["id"]
    run_id = running(api, file_ids=[first["id"]], upload_ids={first["id"]: "reused"})
    with SessionLocal() as db:
        ref = db.scalar(select(MessageFileRef).where(MessageFileRef.run_id == run_id))
        assert ref.reference_origin == "library" and ref.upload_id is None
        assert not db.get(FileObject, first["id"]).meta["upload_provenance"].get("first_message_id")
    other = running(api, file_ids=[first["id"]], upload_ids={first["id"]: "original"})
    with SessionLocal() as db:
        assert db.scalar(select(MessageFileRef).where(MessageFileRef.run_id == other)).reference_origin == "upload"


def test_presign_replays_completion_and_frozen_bytes(api, monkeypatch):
    storage = get_storage()
    monkeypatch.setattr(storage, "presign_put", lambda *a, **kw: "https://upload.example.test")
    body = {"filename": "signed.txt", "client_upload_id": "signed", "size": 6, "sha256": hashlib.sha256(b"frozen").hexdigest()}
    first = api.post("/v1/files/presign", json=body).json()
    assert api.post("/v1/files/presign", json=body).json()["file_id"] == first["file_id"]
    with SessionLocal() as db:
        signed_key = db.get(FileObject, first["file_id"]).storage_key
    storage.put_bytes(signed_key, b"frozen")
    completed = api.post(f"/v1/files/{first['file_id']}/complete")
    assert completed.status_code == 200, completed.text
    storage.put_bytes(signed_key, b"replacement")
    assert api.post(f"/v1/files/{first['file_id']}/complete").json()["id"] == first["file_id"]
    assert api.post("/v1/files/presign", json=body).json()["completed"]
    with SessionLocal() as db:
        assert storage.get_bytes(db.get(FileObject, first["file_id"]).storage_key) == b"frozen"
    ready(first["file_id"])
    second = api.post("/v1/files/presign", json={**body, "client_upload_id": "signed-2"}).json()["file_id"]
    with SessionLocal() as db:
        storage.put_bytes(db.get(FileObject, second).storage_key, b"frozen")
    reused = api.post(f"/v1/files/{second}/complete").json()
    assert reused["id"] == first["file_id"] and reused["reused"]
    assert api.post(f"/v1/files/{second}/complete").json()["id"] == first["file_id"]
    assert api.get("/v1/files?trashed=true").json() == []
    with SessionLocal() as db:
        assert db.get(FileObject, second) is None
    # Old clients also replay the canonical result without exposing a fake trash entry.
    legacy = api.post("/v1/files/presign", json={"filename": "signed.txt", "size": 6}).json()["file_id"]
    with SessionLocal() as db:
        storage.put_bytes(db.get(FileObject, legacy).storage_key, b"frozen")
    assert api.post(f"/v1/files/{legacy}/complete").json()["id"] == first["file_id"]
    assert api.post(f"/v1/files/{legacy}/complete").json()["id"] == first["file_id"]
    assert api.get("/v1/files?trashed=true").json() == []


def test_output_revision_slot_replay_and_legacy_repair(api):
    run_id = running(api)
    a = commit(api, run_id, b"AA", op="one")
    assert a.status_code == 200, a.text
    again = commit(api, run_id, b"AA", op="one")
    assert again.json()["file_id"] == a.json()["file_id"]
    assert commit(api, run_id, b"BB", op="one").status_code == 422
    b = commit(api, run_id, b"BB").json()
    a2 = commit(api, run_id, b"AA").json()
    assert len({a.json()["file_id"], b["file_id"], a2["file_id"]}) == 3
    with SessionLocal() as db:
        head = db.get(FileObject, a2["file_id"])
        assert head.version == 3 and head.is_head
    assert commit(api, run_id, b"AA").json()["file_id"] == a2["file_id"]
    separate = commit(api, run_id, b"AA", key="nested/report.txt").json()
    assert separate["file_id"] != a2["file_id"]
    # Legacy data carries an API-generated storage key but no identity metadata.
    with SessionLocal() as db:
        file = db.get(FileObject, separate["file_id"])
        artifact = db.scalar(select(Artifact).where(Artifact.file_id == file.id))
        artifact.output_key = None
        file.meta = {"source": "sandbox"}  # even the old metadata may have lost its origin run
        db.commit()
        dry = batch(db, limit=500)
        assert file.id in dry["repaired"]
        assert not db.get(FileObject, file.id).meta.get("artifact_filename")
        applied = batch(db, limit=500, apply=True)
        assert file.id in applied["repaired"]
        assert db.get(FileObject, file.id).meta["artifact_filename"] == "report.txt"


def test_listing_is_structured_deep_and_explicit_on_limits(tmp_path):
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    name = nested / "a\t中\n.txt"
    name.write_bytes(b"AB")

    def listing(limit=500, byte_limit=10):
        return subprocess.check_output([sys.executable, "-c", LIST_PROGRAM, str(tmp_path), "hash", str(limit), str(byte_limit)], text=True)

    rows = parse_listing(listing())
    assert rows == [{"path": str(name), "size": 2, "sha256": hashlib.sha256(b"AB").hexdigest()}]
    assert not json.loads(listing(byte_limit=1))["complete"]
    (tmp_path / "second").write_bytes(b"x")
    assert not json.loads(listing(limit=1))["complete"]
    (tmp_path / "link").symlink_to(name)
    assert not json.loads(listing())["complete"]


def test_agent_same_size_aba_and_commit_before_checkpoint_crash():
    class Crash(BaseException):
        pass

    class Platform(FakePlatform):
        receipts = {}

        async def delivery_receipt(self, run_id, identity):
            return self.receipts.get(identity)

    class Broker:
        content = b"AA"
        crash = False
        ids = []

        async def output_manifest(self, lease_id):
            return [
                {"path": "/workspace/outputs/report.txt", "size": len(self.content), "sha256": hashlib.sha256(self.content).hexdigest()}
            ]

        async def commit(self, lease_id, path, expected_sha256=None):
            identity = delivery_id_var.get()
            self.ids.append(identity)
            assert expected_sha256 == hashlib.sha256(self.content).hexdigest()
            result = {"file_id": "fil_" + identity, "sha256": expected_sha256, "size": len(self.content)}
            platform.receipts[identity] = result
            if self.crash:
                raise Crash()
            return result

    async def exercise():
        nonlocal platform
        platform = Platform()
        broker = Broker()
        loop = AgentLoop(platform=platform, broker=broker, llm=ScriptedLLM([]))
        loop._states["run"], loop._steps["run"] = fresh_state(), 0
        turn = _Turn({})
        turn.lease_id = "lease"
        await loop._deliver_leftovers("run", turn)
        await loop._deliver_leftovers("run", turn)
        assert len(broker.ids) == 1
        broker.content = b"BB"
        await loop._deliver_leftovers("run", turn)
        broker.content = b"AA"
        broker.crash = True
        with pytest.raises(Crash):
            await loop._deliver_leftovers("run", turn)
        checkpoint = deepcopy(platform.checkpoint)
        loop._states["run"] = checkpoint["state"]
        loop._steps["run"] = checkpoint["step"]
        turn.delivered = loop._states["run"]["delivered"]
        turn.artifacts = loop._states["run"]["artifacts"]
        # File changes again before recovery; reconcile the prior success before submitting a new revision.
        broker.content, broker.crash = b"CC", False
        await loop._deliver_leftovers("run", turn)
        assert len(broker.ids) == len(set(broker.ids)) == 4
        assert not loop._states["run"]["delivery_intents"]
        assert turn.delivered["/workspace/outputs/report.txt"]["sha256"] == hashlib.sha256(b"CC").hexdigest()

    platform = None
    asyncio.run(exercise())


def test_legacy_collision_recovery_and_sibling_branches_stay_independent(api):
    # Occupy the basename so the first artifact receives a suffix.
    occupied = upload(api).json()
    ready(occupied["id"])
    conversation = api.post("/v1/conversations", json={}).json()["id"]
    response = api.post(f"/v1/conversations/{conversation}/messages", json={"prompt": "first"})
    first_run = response.json()["id"]
    api.post(f"/internal/runs/{first_run}/transition", headers=owned_headers(api, first_run), json={"status": "running"})
    first = commit(api, first_run, b"A", key=None).json()
    assert first["filename"] == "report (2).txt"
    with SessionLocal() as db:
        file = db.get(FileObject, first["file_id"])
        file.meta = {"run_id": first_run, "source": "sandbox"}
        db.commit()
    # Upgrade to a typed slot must use validated provenance, not the display suffix.
    upgraded = commit(api, first_run, b"A")
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["file_id"] == first["file_id"]
    changed = commit(api, first_run, b"B").json()
    assert changed["filename"] == "report (2).txt"
    with SessionLocal() as db:
        assert db.get(FileObject, changed["file_id"]).version == 2
        owner = db.get(Run, first_run).user_id
        left = Run(user_id=owner, conversation_id=conversation, parent_run_id=first_run, prompt="left", status="running", model="test-pro")
        right = Run(
            user_id=owner, conversation_id=conversation, parent_run_id=first_run, prompt="right", status="running", model="test-pro"
        )
        db.add_all([left, right])
        db.flush()
        left_id, right_id = left.id, right.id
        # Use the same job producer/claim as real API-created runs.
        from ailab_platform.api.jobs import stage_job

        stage_job(db, "run", run_id=left_id)
        stage_job(db, "run", run_id=right_id)
        db.commit()
    one = commit(api, left_id, b"L").json()
    two = commit(api, right_id, b"R").json()
    with SessionLocal() as db:
        lf, rf = db.get(FileObject, one["file_id"]), db.get(FileObject, two["file_id"])
        assert lf.is_head and rf.is_head and lf.lineage_id != rf.lineage_id
        assert db.get(FileObject, changed["file_id"]).sha256 == hashlib.sha256(b"B").hexdigest()


def test_presign_changed_fingerprint_and_deleted_result_never_resurrect(api, monkeypatch):
    storage = get_storage()
    monkeypatch.setattr(storage, "presign_put", lambda *a, **kw: "https://upload.example.test")
    body = {"filename": "signed.txt", "client_upload_id": "signed", "size": 2, "sha256": hashlib.sha256(b"AA").hexdigest()}
    reserved = api.post("/v1/files/presign", json=body).json()["file_id"]
    assert api.post("/v1/files/presign", json={**body, "sha256": "b" * 64}).status_code == 422
    with SessionLocal() as db:
        storage.put_bytes(db.get(FileObject, reserved).storage_key, b"BB")
    assert api.post(f"/v1/files/{reserved}/complete").status_code == 400
    with SessionLocal() as db:
        rejected = db.scalar(select(UploadReceipt).where(UploadReceipt.reservation_id == reserved))
        assert rejected.state == "rejected" and rejected.filename is None
    assert api.post("/v1/files/presign", json=body).status_code == 410
    assert api.post(f"/v1/files/{reserved}/complete").status_code == 410


def test_lost_response_after_commit_never_deletes_accepted_object(api, monkeypatch):
    from ailab_platform.api import routes_files

    original = routes_files.file_dict
    failed = False

    def serialize(file):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("response lost after commit")
        return original(file)

    monkeypatch.setattr(routes_files, "file_dict", serialize)
    with pytest.raises(RuntimeError, match="response lost"):
        upload(api, op="lost-serialization")
    with SessionLocal() as db:
        receipt = db.scalar(select(UploadReceipt).where(UploadReceipt.client_upload_id == "lost-serialization"))
        assert receipt is not None
        file = db.get(FileObject, receipt.file_id)
        assert get_storage().get_bytes(file.storage_key) == b"same"
        file_id = file.id
    assert upload(api, op="lost-serialization").json()["id"] == file_id


def test_conversation_fork_preserves_nested_output_identity(api):
    conv = api.post("/v1/conversations", json={}).json()["id"]
    rid = api.post(f"/v1/conversations/{conv}/messages", json={"prompt": "first"}).json()["id"]
    headers = owned_headers(api, rid)
    api.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "running"})
    original = commit(api, rid, b"AA", key="nested/report.txt").json()
    api.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "succeeded"})
    fork = api.post(f"/v1/conversations/{conv}/fork", json={"run_id": rid, "mode": "work"})
    assert fork.status_code == 201, fork.text
    with SessionLocal() as db:
        copied = db.scalar(select(Artifact).join(Run, Run.id == Artifact.run_id).where(Run.conversation_id == fork.json()["id"]))
        assert copied.output_key == "nested/report.txt"
    next_id = api.post(f"/v1/conversations/{fork.json()['id']}/messages", json={"prompt": "next"}).json()["id"]
    api.post(f"/internal/runs/{next_id}/transition", headers=owned_headers(api, next_id), json={"status": "running"})
    unchanged = commit(api, next_id, b"AA", key="nested/report.txt")
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["file_id"] == original["file_id"]


def test_retry_and_ready_reuse_do_not_write_storage_or_charge_quota(api, monkeypatch):
    from ailab_platform.api import routes_files

    first = upload(api, op="first").json()
    ready(first["id"])
    storage = get_storage()
    written = []
    original = storage.put_file

    def put(key, *args):
        written.append(key)
        return original(key, *args)

    monkeypatch.setattr(storage, "put_file", put)

    def forbid_quota(*args):
        raise AssertionError("a reuse must not charge storage again")

    monkeypatch.setattr(routes_files, "enforce_library_quota", forbid_quota)
    assert upload(api, op="first").json()["id"] == first["id"]
    assert upload(api, op="again").json()["id"] == first["id"]
    assert written == []


def test_reused_attachment_keeps_requested_name_after_collision_rename(api):
    project = api.post("/v1/projects", json={"name": "source"}).json()["id"]
    first = upload(api, op="original", project_id=project).json()
    ready(first["id"])
    occupied = upload(api, b"other content", op="occupied").json()
    ready(occupied["id"])
    renamed = api.patch(f"/v1/files/{first['id']}", json={"project_id": None})
    assert renamed.status_code == 200, renamed.text
    reused = upload(api, op="again").json()
    assert reused["id"] == first["id"] and reused["reused"]
    rid = running(api, file_ids=[first["id"]], upload_ids={first["id"]: "again"})
    with SessionLocal() as db:
        ref = db.scalar(select(MessageFileRef).where(MessageFileRef.run_id == rid))
        assert ref.reference_origin == "library" and ref.filename_snapshot == "report.txt"
        assert db.get(FileObject, first["id"]).filename == "report (2).txt"
