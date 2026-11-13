"""Owner-bound document view state and attempt-fenced viewer commands."""

import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..contracts.document_view import DocumentView, ViewState
from ..deps import current_user, get_db
from ..models import MessageFileRef, OutboxMessage, Run, RunDelivery, User
from ..service_auth import require_scope
from . import view_state
from .document_view import checked_ranges, index, normalized
from .routes_conversations import _owned
from .routes_files import _ready_file
from .runtime_fence import fenced_run

router = APIRouter()


def authorize_state(db, user, state):
    if state.get("file_id"):
        file = _ready_file(db, state["file_id"], user)
        if file.deleted_at or file.purpose != "library":
            raise HTTPException(404, "document unavailable")
        if file.sha256 != state["version"]:
            raise HTTPException(409, "document version changed")

    elif state.get("run_id") and state.get("path"):
        from .workspace_documents import request_workspace

        data = request_workspace(db, user, state["run_id"], state["path"], "open")
        if data.get("version") != state["version"]:
            raise HTTPException(409, "workspace version changed")
    else:
        raise HTTPException(422, "document target required")


def active_views(db, run):
    user = db.get(User, run.user_id)
    result = []
    for state in view_state.views(user.id, run.conversation_id):
        try:
            authorize_state(db, user, state)
        except HTTPException:
            continue
        result.append(state)
    return result


@router.put("/v1/conversations/{conversation_id}/view")
def put_view(conversation_id: str, body: ViewState, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _owned(db, conversation_id, user)
    state = body.model_dump(exclude_none=True, exclude={"sequence"})
    try:
        ticket = view_state.begin(user.id, conversation_id, body.view_id, body.sequence)
    except ValueError:
        raise HTTPException(422, "too many view generations") from None
    if ticket is None:
        raise HTTPException(409, "view report superseded or closed")
    prior = next((v for v in view_state.views(user.id, conversation_id) if v["view_id"] == body.view_id), None)
    if body.run_id and prior == state:
        # An unchanged heartbeat extends only an already authorized hint. Recheck the run
        # cheaply here; get_view/capture still verify current workspace bytes before use.
        from .routes_runs import _get_owned_run

        run = _get_owned_run(db, body.run_id, user)
        if run.conversation_id:
            _owned(db, run.conversation_id, user)
        if run.status != "running":
            raise HTTPException(410, "live workspace unavailable")
    else:
        authorize_state(db, user, state)
    if not view_state.commit(user.id, conversation_id, state, ticket):
        raise HTTPException(409, "view report superseded or closed")
    return {"status": "ready"}


@router.delete("/v1/conversations/{conversation_id}/view", status_code=204)
def delete_view(
    conversation_id: str,
    view_id: str = Query(min_length=1, max_length=160),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _owned(db, conversation_id, user)
    try:
        view_state.delete(user.id, conversation_id, view_id)
    except ValueError:
        raise HTTPException(422, "too many view generations") from None
    return Response(status_code=204)


@router.post("/internal/runs/{run_id}/document-view", dependencies=[Depends(require_scope("runs:read"))])
def document_view(
    run_id: str,
    body: DocumentView,
    x_run_attempt: str | None = Header(default=None),
    x_delivery_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")

    with fenced_run(db, run, x_run_attempt):
        if run.status != "running":
            raise HTTPException(409, "run unavailable")
        if body.op == "get_view":
            states = active_views(db, run)
            if body.view_id:
                states = [v for v in states if v["view_id"] == body.view_id]
            return {"status": "ready" if states else "unavailable", "views": states}
        if not x_delivery_id or not x_delivery_id.startswith(run_id + ":") or len(x_delivery_id) > 160:
            raise HTTPException(422, "stable command identity required")
        prior = db.get(RunDelivery, (run_id, x_delivery_id))
        if prior:
            return prior.result
        if bool(body.file_id) == bool(body.path):
            raise HTTPException(422, "one document target required")
        user = db.get(User, run.user_id)
        if body.file_id:
            file = _ready_file(db, body.file_id, user)
            if file.deleted_at or file.purpose != "library":
                raise HTTPException(404, "document unavailable")
            if not db.scalar(
                select(MessageFileRef.id).where(
                    MessageFileRef.run_id == run.id,
                    MessageFileRef.message_id == run.id,
                    MessageFileRef.message_kind == "run_prompt",
                    MessageFileRef.file_id == file.id,
                    MessageFileRef.role == "agent_read",
                )
            ):
                db.add(
                    MessageFileRef(
                        run_id=run.id,
                        message_kind="run_prompt",
                        message_id=run.id,
                        file_id=file.id,
                        role="agent_read",
                        ordinal=0,
                        reference_origin="agent_read",
                    )
                )
            data = index(file)
            target = {"file_id": file.id}
            filename, version = file.filename, file.sha256
            if body.targets:
                locations = checked_ranges(file, [t.at for t in body.targets])
                for target_location, checked in zip(body.targets, locations, strict=True):
                    if target_location.quote and normalized(target_location.quote) not in normalized(checked["text"]):
                        raise HTTPException(422, "quote does not match range")
                    target_location.at = checked["at"]
        else:
            from .workspace_documents import request_workspace

            data = request_workspace(db, user, run.id, body.path, "document")
            if body.targets:
                checked = request_workspace(db, user, run.id, body.path, "check", [t.at for t in body.targets], data.get("version"))
                if len(checked.get("selections", [])) != len(body.targets):
                    raise HTTPException(422, "workspace locator unavailable")
                for location, selected in zip(body.targets, checked["selections"], strict=True):
                    if location.quote and normalized(location.quote) not in normalized(selected["text"]):
                        raise HTTPException(422, "quote does not match range")
                    location.at = selected["at"]
            target = {"path": body.path}
            filename, version = body.path.rsplit("/", 1)[-1], data.get("version")
        if body.version and body.version != version:
            raise HTTPException(409, "document version changed")
        if data.get("status") != "ready":
            return {"status": "unavailable", "reason": data.get("status")}
        if body.op == "capture":
            if body.source == "viewport":
                states = [
                    v
                    for v in active_views(db, run)
                    if v.get("version") == version
                    and (
                        (body.file_id and v.get("file_id") == body.file_id)
                        or (body.path and v.get("run_id") == run.id and v.get("path") == body.path)
                    )
                    and (not body.view_id or v["view_id"] == body.view_id)
                ]
                if len(states) != 1:
                    return {"status": "unavailable", "views": states}
                db.commit()
                return {"status": "capture", "view": states[0]}
            from .viewer_captures import render_capture

            body.version = version
            return render_capture(db, run, body, x_delivery_id)
        command_id = hashlib.sha256(x_delivery_id.encode()).hexdigest()
        payload = {
            "command_id": command_id,
            **target,
            "filename": filename,
            "version": version,
            "targets": [t.model_dump(exclude_none=True) for t in body.targets],
        }
        if body.note:
            payload["note"] = body.note
        result = {"status": "sent", "command_id": command_id, "document": data}
        db.add(RunDelivery(run_id=run.id, delivery_id=x_delivery_id, result=result))
        db.add(
            OutboxMessage(
                id="view_" + command_id[:32],
                topic="viewer.event",
                payload={"run_id": run.id, "type": "document.reveal", "payload": payload},
            )
        )
        db.commit()
        return result


@router.get("/internal/runs/{run_id}/captures/{capture_id}", dependencies=[Depends(require_scope("runs:read"))])
def capture_image(run_id: str, capture_id: str, db: Session = Depends(get_db)):
    from .viewer_captures import image

    return image(db, run_id, capture_id)
