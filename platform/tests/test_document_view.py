"""Viewer versions, source authorization, frozen selections and bounded browser captures."""

import base64
import hashlib
import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from ailab_platform.api import view_state
from ailab_platform.api.document_sources import index_document
from ailab_platform.api.preview_dispatch import dispatch_preview_jobs
from ailab_platform.contracts.document_view import PARSER_REVISION
from ailab_platform.db import SessionLocal
from ailab_platform.models import Conversation, FileObject, MessageDocRef, Run, RunEvent, User
from ailab_platform.storage import get_storage
from ailab_platform.worker.viewer import build
from tests.test_ledger_protocol import AUTH, SERVICE, api  # noqa: F401
from tests.test_runtime_protocol import owned


@pytest.fixture(autouse=True)
def recorded_native_ranges(monkeypatch):
    from ailab_platform.api import document_view as viewer

    def checked(file, locations):
        with SessionLocal() as db:
            from fastapi import HTTPException

            from ailab_platform.models import DocumentBlock, FileDerivation

            derivation = db.scalar(select(FileDerivation).where(FileDerivation.file_id == file.id))
            result = []
            for at in locations:
                block = db.scalar(select(DocumentBlock).where(DocumentBlock.derivation_id == derivation.id, DocumentBlock.anchor == at))
                if not block:
                    raise HTTPException(422, "invalid location")
                result.append({"at": at, "text": get_storage().get_bytes(block.text_key).decode()})
            return result

    monkeypatch.setattr(viewer, "checked_ranges", checked)
    monkeypatch.setattr("ailab_platform.api.routes_document_view.checked_ranges", checked)


def document(api):  # noqa: F811
    uploaded = api.post(
        "/v1/files", headers=AUTH, files={"upload": ("view.docx", b"not parsed by this fixture", "application/octet-stream")}
    )
    assert uploaded.status_code == 201, uploaded.text
    fid = uploaded.json()["id"]
    storage = get_storage()
    prefix = f"derived/{fid}/office/fixture/"
    text = "A verified paragraph with quoted words."
    storage.put_bytes(prefix + "000000.txt", text.encode())
    manifest = {
        "parser": "fixture-reader",
        "kind": "docx",
        "status": "ready",
        "total_units": 1,
        "outline": [],
        "units": [
            {
                "ordinal": 0,
                "anchor": "p:1",
                "name": "000000.txt",
                "unit_number": 1,
                "chars": len(text),
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        ],
    }
    storage.put_bytes(prefix + "manifest.json", json.dumps(manifest).encode())
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.status = "ready"
        file.meta = {"processing": "done", "scan": {"verdict": "clean"}, "extract": {"document_manifest_key": prefix + "manifest.json"}}
        index_document(db, file, prefix + "manifest.json")
        version = file.sha256
        from sqlalchemy import delete

        from ailab_platform.models import OutboxMessage

        # The fixture installs a completed representation directly, so retire
        # the upload job exactly as a successful Worker ACK would.
        db.execute(
            delete(OutboxMessage).where(
                OutboxMessage.payload["file_id"].as_string() == fid, OutboxMessage.payload["kind"].as_string() == "file"
            )
        )
        storage.put_bytes(
            f"derived/{fid}/viewer.json",
            json.dumps(
                build(fid, version, file.filename, file.size, {"kind": "docx", "units": 1, "blocks": [{"at": "p:1", "text": text}]})
            ).encode(),
        )
        db.commit()
    return fid, version, text


def conv(api):  # noqa: F811
    response = api.post("/v1/conversations", headers=AUTH, json={})
    assert response.status_code == 201
    return response.json()["id"]


def reference(fid, version):
    return {"file_id": fid, "version": version, "at": "p:1", "quote": "quoted words"}


def send(api, cid, refs, **extra):  # noqa: F811
    return api.post(
        f"/v1/conversations/{cid}/messages",
        headers=AUTH,
        json={"prompt": "Explain this selection", "model": "test-fast", "mode": "chat", "doc_refs": refs, **extra},
    )


def view(api, cid, fid, version, view_id="view"):  # noqa: F811
    state = {"view_id": view_id, "file_id": fid, "version": version, "at": "p:1"}
    response = api.put(f"/v1/conversations/{cid}/view", headers=AUTH, json=state)
    assert response.status_code == 200, response.text
    return state


def test_document_index_and_deleted_source(api):  # noqa: F811
    fid, version, _ = document(api)
    result = api.get(f"/v1/files/{fid}/document", headers=AUTH)
    assert result.status_code == 200
    assert result.json()["parser_revision"] == PARSER_REVISION
    assert result.json()["version"] == version and result.json()["native"]
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.deleted_at = datetime.now(UTC)
        db.commit()
    assert api.get(f"/v1/files/{fid}/document", headers=AUTH).status_code == 404


def test_refs_are_frozen_deduplicated_and_sent_as_selection_only(api):  # noqa: F811
    fid, version, _ = document(api)
    cid = conv(api)
    ref = reference(fid, version)
    first = send(api, cid, [ref], client_send_id="send-" + uuid4().hex)
    assert first.status_code == 201, first.text
    rid = first.json()["id"]
    assert first.json()["doc_refs"] == [{**ref, "filename": api.get(f"/v1/files/{fid}", headers=AUTH).json()["filename"]}]
    assert first.json()["input_file_ids"] == []
    with SessionLocal() as db:
        send_id = db.get(Run, rid).client_send_id
    assert send(api, cid, [ref], client_send_id=send_id).json()["id"] == rid
    changed = {**ref, "quote": "verified paragraph"}
    assert send(api, cid, [changed], client_send_id=send_id).status_code == 409
    data = api.get(f"/internal/runs/{rid}", headers=SERVICE).json()
    assert data["input_files"] == []
    assert data["doc_refs"][0]["quote"] == ref["quote"]
    with SessionLocal() as db:
        assert len(list(db.scalars(select(MessageDocRef).where(MessageDocRef.run_id == rid)))) == 1


@pytest.mark.parametrize("quote", ["quoted words", ""])
@pytest.mark.parametrize("change,status", [({"version": "stale"}, 409), ({"at": "p:2"}, 422)])
def test_reference_identity_is_checked_without_quoted_text(api, quote, change, status):  # noqa: F811
    fid, version, _ = document(api)
    response = send(api, conv(api), [{**reference(fid, version), "quote": quote, **change}])
    assert response.status_code == status, response.text


@pytest.mark.parametrize("quote", ["", "quoted words"])
def test_location_reference_survives_send_and_retry(api, quote):  # noqa: F811
    from tests.test_conversations import _finish

    fid, version, _ = document(api)
    cid = conv(api)
    ref = {**reference(fid, version), "quote": quote}
    first = send(api, cid, [ref])
    assert first.status_code == 201, first.text
    assert first.json()["doc_refs"][0]["quote"] == quote
    _finish(api, first.json()["id"], "done")
    retry = api.post(f"/v1/runs/{first.json()['id']}/retry", headers=AUTH, json={})
    assert retry.status_code == 201, retry.text
    assert retry.json()["doc_refs"] == first.json()["doc_refs"]


@pytest.mark.parametrize("text", ["", "chart data"])
def test_workspace_shape_reference_without_text(api, monkeypatch, text):  # noqa: F811
    from ailab_platform.api.document_view import validate_refs
    from ailab_platform.contracts.document_view import DocRef

    rid, _, _ = owned(api)
    calls = []

    def workspace(db, user, run_id, path, operation, selections, version):
        calls.append((run_id, path, operation, selections, version))
        return {"version": "v1", "selections": [{"at": "slide:2/shape:3", "text": text}]}

    monkeypatch.setattr("ailab_platform.api.workspace_documents.request_workspace", workspace)
    ref = {"run_id": rid, "path": "outputs/chart.pptx", "version": "v1", "at": "slide:2/shape:3", "quote": ""}
    with SessionLocal() as db:
        user = db.get(User, db.get(Run, rid).user_id)
        assert validate_refs(db, user, [DocRef.model_validate(ref)]) == [{**ref, "filename": "chart.pptx"}]
    assert calls == [(rid, ref["path"], "check", [ref["at"]], "v1")]


@pytest.mark.parametrize(
    "change,status", [({"version": "stale"}, 409), ({"at": "p:2"}, 422), ({"quote": "invented"}, 422), ({"quote": " \n"}, 422)]
)
def test_invalid_reference_rejected_before_send(api, change, status):  # noqa: F811
    fid, version, _ = document(api)
    response = send(api, conv(api), [{**reference(fid, version), **change}])
    assert response.status_code == status, response.text


def test_reference_limits_and_ownership(api):  # noqa: F811
    fid, version, _ = document(api)
    cid = conv(api)
    ref = reference(fid, version)
    assert send(api, cid, [ref] * 9).status_code == 422
    assert send(api, cid, [{**ref, "quote": "x" * 4001}]).status_code == 422
    with SessionLocal() as db:
        other = User(email=uuid4().hex + "@test.local", display_name="other")
        db.add(other)
        db.flush()
        db.get(FileObject, fid).user_id = other.id
        db.commit()
    assert send(api, cid, [ref]).status_code == 404
    assert api.get(f"/v1/files/{fid}/document", headers=AUTH).status_code == 404


def test_view_state_ttl_isolation_and_no_events(api, monkeypatch):  # noqa: F811
    fid, version, _ = document(api)
    cid = conv(api)
    state = view(api, cid, fid, version)
    with SessionLocal() as db:
        owner = db.get(Conversation, cid).user_id
    assert view_state.views(owner, cid) == [state]
    assert view_state.views("someone-else", cid) == []
    assert view_state.views(owner, "another-conversation") == []
    clock = view_state.time.time()
    monkeypatch.setattr(view_state.time, "time", lambda: clock + 601)
    assert view_state.views(owner, cid) == []
    assert api.delete(f"/v1/conversations/{cid}/view?view_id=view", headers=AUTH).status_code == 204


def test_workspace_heartbeat_is_cheap_but_consumers_revalidate_current_bytes(api, monkeypatch):  # noqa: F811
    rid, _, headers = owned(api)
    cid = conv(api)
    with SessionLocal() as db:
        run = db.get(Run, rid)
        run.conversation_id = cid
        owner = run.user_id
        db.commit()
    requests = []

    def workspace(*args):
        requests.append(args)
        return {"version": "v1"}

    monkeypatch.setattr("ailab_platform.api.workspace_documents.request_workspace", workspace)
    now = [1000]
    monkeypatch.setattr(view_state, "time", SimpleNamespace(time=lambda: now[0]))
    state = {"view_id": "workspace", "run_id": rid, "path": "outputs/a.docx", "version": "v1", "at": "p:1"}
    url = f"/v1/conversations/{cid}/view"
    assert api.put(url, headers=AUTH, json=state).status_code == 200
    assert len(requests) == 1
    for _ in range(4):
        now[0] += 180
        assert api.put(url, headers=AUTH, json=state).status_code == 200
    assert view_state.views(owner, cid) == [state]
    assert len(requests) == 1  # no Broker snapshots, hashing, parsing or rendering for unchanged heartbeats
    response = api.post(f"/internal/runs/{rid}/document-view", headers=headers, json={"op": "get_view"})
    assert response.status_code == 200 and response.json()["views"] == [state]
    assert len(requests) == 2
    assert api.put(url, headers=AUTH, json={**state, "at": "p:2"}).status_code == 200
    assert len(requests) == 3
    assert api.put(url, headers=AUTH, json={**state, "version": "v2"}).status_code == 409
    assert len(requests) == 4
    state["at"] = "p:2"
    now[0] += 601
    assert api.put(url, headers=AUTH, json=state).status_code == 200
    assert len(requests) == 5  # expired state must pass full validation again
    with SessionLocal() as db:
        db.get(Run, rid).status = "succeeded"
        db.commit()
    assert api.put(url, headers=AUTH, json=state).status_code == 410
    assert len(requests) == 5


def test_reveal_outbox_is_fenced_and_redelivery_is_idempotent(api):  # noqa: F811
    fid, version, _ = document(api)
    rid, _, headers = owned(api)
    url = f"/internal/runs/{rid}/document-view"
    payload = {"op": "reveal", "file_id": fid, "targets": [{"at": "p:1"}]}
    assert api.post(url, headers=SERVICE, json=payload).status_code == 409
    headers = {**headers, "X-Delivery-Id": rid + ":reveal"}
    response = api.post(url, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent"
    assert api.post(url, headers=headers, json=payload).json() == response.json()
    dispatch_preview_jobs()
    dispatch_preview_jobs()
    with SessionLocal() as db:
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == rid, RunEvent.type == "document.reveal")))
        assert len(events) == 1
        assert events[0].payload == {
            "command_id": response.json()["command_id"],
            "file_id": fid,
            "filename": response.json()["document"]["title"],
            "version": version,
            "targets": [{"at": "p:1"}],
        }


def capture_setup(api):  # noqa: F811
    fid, version, _ = document(api)
    rid, _, headers = owned(api)
    cid = conv(api)
    with SessionLocal() as db:
        run = db.get(Run, rid)
        run.conversation_id = cid
        run.options = {**run.options, "client": "portal"}
        db.commit()
    from ailab_platform.models import DerivedBudget

    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.meta = {**file.meta, "scan": {"verdict": "clean"}}
        db.get(DerivedBudget, 1).backfill_complete = True
        db.commit()
    view(api, cid, fid, version)
    response = api.post(
        f"/internal/runs/{rid}/client-calls",
        headers=headers,
        json={"call_id": "capture", "kind": "capture_view", "payload": {"input": {"view_id": "view"}, "timeout_ms": 10000}},
    )
    assert response.status_code == 200, response.text
    token = api.post(f"/v1/runs/{rid}/client-calls/capture/claim", headers=AUTH).json()["claim_token"]
    image = io.BytesIO()
    Image.new("RGB", (4, 3), "white").save(image, format="PNG")
    value = {
        "image": base64.b64encode(image.getvalue()).decode(),
        "mime_type": "image/png",
        "width": 4,
        "height": 3,
        "at": "p:1",
        "version": version,
    }
    return rid, cid, fid, version, token, value


def test_viewport_capture_png_version_and_claim(api):  # noqa: F811
    rid, _cid, fid, _version, token, value = capture_setup(api)
    path = f"/v1/runs/{rid}/client-calls/capture/result"
    body = {"claim_token": token, "ok": True, "value": {**value, "version": "changed"}}
    assert api.post(path, headers=AUTH, json=body).status_code == 409
    body["value"] = {**value, "image": base64.b64encode(b"not png").decode()}
    assert api.post(path, headers=AUTH, json=body).status_code == 422
    body["value"] = value
    result = api.post(path, headers=AUTH, json=body)
    assert result.status_code == 200, result.text
    assert api.post(path, headers=AUTH, json=body).status_code == 409
    state = api.get(f"/internal/runs/{rid}/client-calls/capture", headers=SERVICE).json()
    capture_id = state["result"]["value"]["capture_id"]
    image = api.get(f"/internal/runs/{rid}/captures/{capture_id}", headers=SERVICE)
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    with SessionLocal() as db:
        db.get(FileObject, fid).deleted_at = datetime.now(UTC)
        db.commit()
    assert api.get(f"/internal/runs/{rid}/captures/{capture_id}", headers=SERVICE).status_code == 404


def test_capture_rejects_view_switched_in_flight(api):  # noqa: F811
    rid, cid, _fid, _version, token, value = capture_setup(api)
    assert api.delete(f"/v1/conversations/{cid}/view?view_id=view", headers=AUTH).status_code == 204
    assert (
        api.post(
            f"/v1/runs/{rid}/client-calls/capture/result", headers=AUTH, json={"claim_token": token, "ok": True, "value": value}
        ).status_code
        == 409
    )


def test_capture_is_inherited_only_by_authorized_descendant(api):  # noqa: F811
    rid, cid, fid, _version, token, value = capture_setup(api)
    assert (
        api.post(
            f"/v1/runs/{rid}/client-calls/capture/result", headers=AUTH, json={"claim_token": token, "ok": True, "value": value}
        ).status_code
        == 200
    )
    capture = api.get(f"/internal/runs/{rid}/client-calls/capture", headers=SERVICE).json()["result"]["value"]["capture_id"]
    child, _, _ = owned(api)
    unrelated, _, _ = owned(api)
    with SessionLocal() as db:
        run = db.get(Run, child)
        run.conversation_id, run.parent_run_id = cid, rid
        db.get(Run, unrelated).conversation_id = cid
        db.commit()
    path = f"/internal/runs/{child}/captures/{capture}"
    original = api.get(f"/internal/runs/{rid}/captures/{capture}", headers=SERVICE)
    inherited = api.get(path, headers=SERVICE)
    assert inherited.status_code == 200 and inherited.content == original.content
    assert api.get(f"/internal/runs/{unrelated}/captures/{capture}", headers=SERVICE).status_code == 404
    with SessionLocal() as db:
        db.get(FileObject, fid).deleted_at = datetime.now(UTC)
        db.commit()
    assert api.get(path, headers=SERVICE).status_code == 404


def test_native_admission_has_explicit_byte_and_unit_bounds():
    manifest = {"kind": "pdf", "units": 300}
    assert build("f", "hash", "test.pdf", 20 * 1024**2, manifest)["native"]
    assert not build("f", "hash", "test.pdf", 20 * 1024**2 + 1, manifest)["native"]
    manifest["units"] = 301
    assert not build("f", "hash", "test.pdf", 1, manifest)["native"]


def test_retry_edit_fork_preserve_frozen_references(api):  # noqa: F811
    from tests.test_conversations import _finish

    fid, version, _ = document(api)
    cid = conv(api)
    first = send(api, cid, [reference(fid, version)]).json()
    _finish(api, first["id"], "done")
    retry = api.post(f"/v1/runs/{first['id']}/retry", headers=AUTH, json={})
    assert retry.status_code == 201, retry.text
    assert retry.json()["doc_refs"] == first["doc_refs"]
    _finish(api, retry.json()["id"], "retried")
    edited = api.post(f"/v1/runs/{retry.json()['id']}/edit", headers=AUTH, json={"prompt": "new prompt"})
    assert edited.status_code == 201, edited.text
    assert edited.json()["doc_refs"] == first["doc_refs"]
    _finish(api, edited.json()["id"], "edited")
    fork = api.post(f"/v1/conversations/{cid}/fork", headers=AUTH, json={"run_id": edited.json()["id"], "mode": "chat"})
    assert fork.status_code == 201, fork.text
    turns = api.get(f"/v1/conversations/{fork.json()['id']}", headers=AUTH).json()["turns"]
    assert turns[-1]["doc_refs"] == first["doc_refs"]


@pytest.mark.parametrize("quote", ["quoted words", ""])
def test_queue_keeps_selection_after_dispatch(api, quote):  # noqa: F811
    from tests.test_conversations import _finish

    fid, version, _ = document(api)
    cid = conv(api)
    first = send(api, cid, []).json()
    queued = api.post(
        f"/v1/conversations/{cid}/queue",
        headers=AUTH,
        json={"prompt": "queued quote", "model": "test-fast", "mode": "chat", "doc_refs": [{**reference(fid, version), "quote": quote}]},
    )
    assert queued.status_code == 201, queued.text
    assert queued.json()["doc_refs"][0]["quote"] == quote
    _finish(api, first["id"], "done")
    turns = api.get(f"/v1/conversations/{cid}", headers=AUTH).json()["turns"]
    assert turns[-1]["doc_refs"] == queued.json()["doc_refs"]


def test_workspace_document_requires_owner_and_live_run(api, monkeypatch):  # noqa: F811
    cid = conv(api)
    run = send(api, cid, []).json()
    assert api.get(f"/v1/runs/{run['id']}/workspace/document?path=a.docx", headers=AUTH).status_code == 410
    assert api.get(f"/v1/runs/{run['id']}/workspace/preview?path=a.docx", headers=AUTH).status_code == 410
    from tests.test_conversations import _other_user

    other = _other_user(api)
    assert api.get(f"/v1/runs/{run['id']}/workspace/content?path=a.docx", headers=other).status_code == 404
    assert api.get(f"/v1/runs/{run['id']}/workspace/preview?path=a.docx", headers=other).status_code == 404


def test_file_share_viewer_keeps_existing_share_fence(api):  # noqa: F811
    from tests.test_conversations import _other_user

    fid, version, _ = document(api)
    recipient = _other_user(api)
    recipient_id = api.get("/v1/me", headers=recipient).json()["id"]
    share = api.post("/v1/shares", headers=AUTH, json={"kind": "file", "file_id": fid, "audience": "people", "user_ids": [recipient_id]})
    assert share.status_code == 201, share.text
    url = f"/v1/shares/{share.json()['id']}/files/{fid}/document"
    response = api.get(url, headers=recipient)
    assert response.status_code == 200 and response.json()["version"] == version
    assert api.delete(f"/v1/shares/{share.json()['id']}", headers=AUTH).status_code == 204
    assert api.get(url, headers=recipient).status_code == 404


def test_workspace_report_is_not_resurrected_after_close_or_reordered(api, monkeypatch):  # noqa: F811
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from fastapi import HTTPException

    from ailab_platform.api import routes_document_view as route
    from ailab_platform.contracts.document_view import ViewState

    entered, resume = threading.Event(), threading.Event()
    user = SimpleNamespace(id=uuid4().hex)
    cid = uuid4().hex
    body = ViewState(view_id="blocked", run_id="r", path="outputs/a.docx", version="v", at="p:1", sequence=1)
    monkeypatch.setattr(route, "_owned", lambda *_: None)

    def authorize(_db, _user, value):
        if value["at"] == "p:1":
            entered.set()
            assert resume.wait(5)

    monkeypatch.setattr(route, "authorize_state", authorize)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(route.put_view, cid, body, user, None)
        assert entered.wait(5)
        route.delete_view(cid, "blocked", user, None)
        route.delete_view(cid, "blocked", user, None)
        resume.set()
        with pytest.raises(HTTPException) as error:
            pending.result(timeout=5)
        assert error.value.status_code == 409
    assert view_state.views(user.id, cid) == []
    entered.clear()
    resume.clear()
    body = body.model_copy(update={"view_id": "ordered"})
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(route.put_view, cid, body, user, None)
        assert entered.wait(5)
        assert route.put_view(cid, body.model_copy(update={"at": "p:2", "sequence": 2}), user, None) == {"status": "ready"}
        resume.set()
        with pytest.raises(HTTPException) as error:
            pending.result(timeout=5)
        assert error.value.status_code == 409
    assert view_state.views(user.id, cid)[0]["at"] == "p:2"


def test_opening_metadata_does_not_start_viewer_or_native_jobs_and_structure_remains_explicit(api):  # noqa: F811
    from ailab_platform.models import OutboxMessage

    fid, version, _ = document(api)
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.meta = {"processing": "done", "scan": {"verdict": "clean"}}
        db.commit()
        get_storage().delete(f"derived/{fid}/viewer.json")
    response = api.get(f"/v1/files/{fid}/document", headers=AUTH)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["native"] and value["version"] == version
    assert value["structure"] == "not_prepared" and value["units"] is None
    assert value["outline"] == [] and "blocks" not in value
    with SessionLocal() as db:
        assert db.scalar(select(OutboxMessage.id).where(OutboxMessage.payload["file_id"].as_string() == fid)) is None
    requested = api.get(f"/v1/files/{fid}/document?structure=true", headers=AUTH)
    assert requested.status_code == 200 and requested.json()["status"] == "pending"
    with SessionLocal() as db:
        assert db.scalar(select(OutboxMessage.id).where(OutboxMessage.payload["file_id"].as_string() == fid)) is not None


def test_history_rebuilds_the_exact_text_the_model_first_read(api):  # noqa: F811
    """Selected passages, an offsetless skill and a schedule header: both history endpoints repeat the live text."""
    from tests.test_conversations import _finish

    from ailab_platform.agent.prompts import history_messages, user_text

    fid, version, _ = document(api)
    cid = conv(api)
    first = send(api, cid, [reference(fid, version)], skills=[{"name": "pptx"}]).json()
    schedule = {"title": "周报", "rule": "每周一 09:00", "at": "2026-11-16 09:00"}
    with SessionLocal() as db:
        run = db.get(Run, first["id"])
        run.options = {**run.options, "schedule": schedule}
        db.commit()
    live = api.get(f"/internal/runs/{first['id']}", headers=SERVICE).json()
    expected = user_text(live["prompt"], skills=live["options"]["skills"], refs=live["doc_refs"], schedule=schedule)
    assert expected.endswith("Explain this selection[技能：PPT 设计]\n\n<document_references>\nview.docx (p:1)\nquoted words\n</document_references>")
    _finish(api, first["id"], "done")
    later = send(api, cid, []).json()["id"]
    for path in (f"/internal/runs/{later}", f"/internal/runs/{later}/model-context"):
        history = api.get(path, headers=SERVICE).json()["history"]
        assert history_messages(history)[0] == {"role": "user", "content": expected}, path
