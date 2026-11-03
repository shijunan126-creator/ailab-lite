"""Proxy bounded workspace data after API ownership authorization."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..config import settings
from ..deps import current_user, get_db
from ..models import User
from .routes_files import _ClosingStreamingResponse
from .routes_runs import _get_owned_run

router = APIRouter()


class WorkspaceStream:
    def __init__(self, response, client):
        self.response, self.client = response, client
        self.chunks = response.iter_bytes(65536)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.chunks)

    def close(self):
        self.response.close()
        self.client.close()


def request_workspace(db, user, run_id, path, operation, selections=None, version=None):
    run = _get_owned_run(db, run_id, user)
    if run.conversation_id:
        from .routes_conversations import _owned

        _owned(db, run.conversation_id, user)
    if run.status != "running":
        raise HTTPException(410, "live workspace unavailable")
    client = httpx.Client(timeout=httpx.Timeout(90, connect=3), trust_env=False)
    response = None
    try:
        request = client.build_request(
            "POST",
            f"{settings.broker_base_url}/internal/runs/{run.id}/workspace/{operation}",
            headers={"X-Service-Token": settings.caller_token("api")},
            json={
                "user_id": user.id,
                "path": path,
                **({"selections": selections} if selections else {}),
                **({"version": version} if version else {}),
            },
        )
        response = client.send(request, stream=True)
        if response.status_code != 200:
            raise HTTPException(response.status_code, "workspace unavailable")
        if operation != "content":
            response.read()
            return response.json()

        db.close()
        return _ClosingStreamingResponse(
            WorkspaceStream(response, client),
            media_type="application/octet-stream",
            headers={"Cache-Control": "private, no-store", "ETag": response.headers.get("ETag", "")},
        )
    except BaseException:
        if response is not None:
            response.close()
        client.close()
        raise
    finally:
        if operation != "content":
            if response is not None:
                response.close()
            client.close()


@router.get("/v1/runs/{run_id}/workspace/document")
def document(
    run_id: str, path: str = Query(min_length=1, max_length=1000), user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return request_workspace(db, user, run_id, path, "document")


@router.get("/v1/runs/{run_id}/workspace/content")
def content(
    run_id: str, path: str = Query(min_length=1, max_length=1000), user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return request_workspace(db, user, run_id, path, "content")


@router.get("/v1/runs/{run_id}/workspace/preview")
def preview(
    run_id: str, path: str = Query(min_length=1, max_length=1000), user: User = Depends(current_user), db: Session = Depends(get_db)
):
    manifest = request_workspace(db, user, run_id, path, "preview")
    if manifest.get("error"):
        raise HTTPException(422, "预览生成失败")
    return manifest


def workspace_reference(db, user, ref):
    index = request_workspace(db, user, ref.run_id, ref.path, "check", [ref.at], ref.version)
    if index.get("version") != ref.version:
        raise HTTPException(409, "workspace version changed")
    selections = index.get("selections") or []
    if len(selections) != 1:
        raise HTTPException(422, "workspace locator unavailable")
    return ref.path.rsplit("/", 1)[-1], selections[0]["text"]
