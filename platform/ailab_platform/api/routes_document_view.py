"""Owner-bound document view state and attempt-fenced viewer commands."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..contracts.document_view import ViewState
from ..deps import current_user, get_db
from ..models import User
from ..service_auth import require_scope
from . import view_state
from .routes_conversations import _owned
from .routes_files import _ready_file

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


@router.get("/internal/runs/{run_id}/captures/{capture_id}", dependencies=[Depends(require_scope("runs:read"))])
def capture_image(run_id: str, capture_id: str, db: Session = Depends(get_db)):
    from .viewer_captures import image

    return image(db, run_id, capture_id)
