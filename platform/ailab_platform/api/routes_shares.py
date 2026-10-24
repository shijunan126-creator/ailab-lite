"""Principal-authorized read-only snapshots. Never expose run events or owner file URLs."""

from contextlib import ExitStack, contextmanager
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, delete, exists, or_, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..conversation_tree import path_ids
from ..deps import current_user, get_db
from ..locks import user_critical_section
from ..models import Artifact, Conversation, FileObject, FileStatus, Run, RunStatus, Share, ShareRecipient, User, utcnow
from ..schemas import UTCModel
from . import routes_files
from .inbox import create_notification

router = APIRouter(tags=["shares"])


class ShareRecipientOut(BaseModel):
    id: str
    display_name: str
    username: str | None


class ShareOut(UTCModel):
    id: str
    kind: str
    conversation_id: str | None
    through_run_id: str | None
    file_id: str | None
    audience: str
    recipients: list[ShareRecipientOut]
    title: str
    url: str
    created_at: datetime
    updated_at: datetime


class ShareList(BaseModel):
    items: list[ShareOut]
    next_cursor: str | None


class ShareFile(BaseModel):
    id: str
    file_id: str
    filename: str
    content_type: str
    size: int
    content_url: str
    download_url: str
    preview_url: str
    thumbnail_url: str


class AttachmentName(BaseModel):
    filename: str


class ShareTurn(UTCModel):
    id: str
    prompt: str
    answer: str
    created_at: datetime
    mode: str
    artifacts: list[ShareFile]
    attachments: list[AttachmentName]


class ShareOwner(BaseModel):
    display_name: str


class ShareView(BaseModel):
    share: ShareOut
    owner: ShareOwner
    title: str
    turns: list[ShareTurn]
    next_cursor: str | None
    file: ShareFile | None = None


class CreateShare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["conversation", "file"]
    conversation_id: str | None = Field(default=None, max_length=40)
    file_id: str | None = Field(default=None, max_length=40)
    audience: Literal["people", "everyone"]
    user_ids: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def target(self):
        if (self.kind == "conversation" and (not self.conversation_id or self.file_id)) or (
            self.kind == "file" and (not self.file_id or self.conversation_id)
        ):
            raise ValueError("exactly one target matching kind is required")
        return self


class PatchShare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audience: Literal["people", "everyone"] | None = None
    user_ids: list[str] | None = Field(default=None, max_length=200)
    refresh: Literal[True] | None = None


def live_conditions():
    return (
        Share.revoked_at.is_(None),
        exists(select(User.id).where(User.id == Share.owner_id, User.disabled_at.is_(None))),
        or_(
            and_(
                Share.kind == "conversation",
                exists(
                    select(Conversation.id).where(
                        Conversation.id == Share.conversation_id, Conversation.user_id == Share.owner_id, Conversation.deleted_at.is_(None)
                    )
                ),
            ),
            and_(
                Share.kind == "file",
                exists(
                    select(FileObject.id).where(
                        FileObject.id == Share.file_id,
                        FileObject.user_id == Share.owner_id,
                        FileObject.deleted_at.is_(None),
                        FileObject.purged_at.is_(None),
                        FileObject.status == FileStatus.READY,
                    )
                ),
            ),
        ),
    )


def received(user_id):
    return or_(
        Share.audience == "everyone",
        Share.id.in_(select(ShareRecipient.share_id).where(ShareRecipient.user_id == user_id)),
    )


def authorized(db, share_id, user, owner_only=False):
    row = db.scalar(
        select(Share).where(
            Share.id == share_id,
            *live_conditions(),
            Share.owner_id == user.id if owner_only else or_(Share.owner_id == user.id, received(user.id)),
        )
    )
    if not row:
        raise HTTPException(404, "share not found")
    return row


def share_out(db, row):
    recipients = (
        db.execute(
            select(User.id, User.display_name, User.username)
            .join(ShareRecipient, ShareRecipient.user_id == User.id)
            .where(ShareRecipient.share_id == row.id)
            .order_by(User.id)
            .limit(200)
        )
        .mappings()
        .all()
    )
    return {
        "id": row.id,
        "kind": row.kind,
        "conversation_id": row.conversation_id,
        "file_id": row.file_id,
        "audience": row.audience,
        "recipients": [dict(r) for r in recipients],
        "title": row.title,
        "url": f"/s/{row.id}",
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "through_run_id": row.through_run_id,
    }


def snapshot(db, row):
    if row.kind == "file":
        f = db.get(FileObject, row.file_id)
        if not f or f.user_id != row.owner_id or f.deleted_at or f.purged_at or f.status != FileStatus.READY or f.purpose != "library":
            raise HTTPException(404, "file not found")
        row.title = f.filename[:200]
    else:
        conv = db.get(Conversation, row.conversation_id)
        if not conv or conv.user_id != row.owner_id or conv.deleted_at:
            raise HTTPException(404, "conversation not found")
        leaf = db.get(Run, conv.active_run_id) if conv.active_run_id else None
        if not leaf or leaf.conversation_id != conv.id or leaf.user_id != row.owner_id or leaf.status not in RunStatus.TERMINAL:
            raise HTTPException(409, "分享需要已结束的活动轮次")
        path_ids(db, conv.id, leaf.id)
        row.through_run_id, row.title = leaf.id, conv.title


def set_recipients(db, row, ids):
    ids = set(ids) - {row.owner_id}
    users = list(db.scalars(select(User).where(User.id.in_(ids), User.disabled_at.is_(None))))
    if len(users) != len(ids):
        raise HTTPException(422, "接收人不存在或已停用")
    old = set(db.scalars(select(ShareRecipient.user_id).where(ShareRecipient.share_id == row.id)))
    db.execute(delete(ShareRecipient).where(ShareRecipient.share_id == row.id))
    for user in users:
        db.add(ShareRecipient(share_id=row.id, user_id=user.id))
        if user.id not in old:
            create_notification(db, user.id, "share.received", row.title, conversation_id=row.conversation_id, share_id=row.id)


@router.get("/v1/users/lookup")
def lookup(q: str = Query(min_length=1, max_length=64), user: User = Depends(current_user), db: Session = Depends(get_db)):
    # Range predicates use ordinary B-tree indexes on both databases, including non-ASCII prefixes.
    upper = q[:-1] + chr(ord(q[-1]) + 1) if ord(q[-1]) < 0x10FFFF else q + "\U0010ffff"
    rows = (
        db.execute(
            select(User.id, User.username, User.display_name)
            .where(
                User.disabled_at.is_(None),
                User.id != user.id,
                or_(and_(User.username >= q, User.username < upper), and_(User.display_name >= q, User.display_name < upper)),
            )
            .order_by(User.username, User.id)
            .limit(8)
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


@contextmanager
def sharing_locks(db, owner_id, recipient_ids):
    # Acquire every user lock before writes/FK locks, in one global order.
    # Notification insertion can reenter these locks and prune atomically.
    with ExitStack() as stack:
        for uid in sorted({owner_id, *(recipient_ids or [])}):
            stack.enter_context(user_critical_section(db, uid))
        yield


@router.post("/v1/shares", status_code=201, response_model=ShareOut)
def create(body: CreateShare, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with sharing_locks(db, user.id, body.user_ids if body.audience == "people" else []):
        row = db.scalar(
            select(Share)
            .where(
                Share.owner_id == user.id,
                Share.revoked_at.is_(None),
                Share.kind == body.kind,
                Share.conversation_id == body.conversation_id if body.kind == "conversation" else Share.file_id == body.file_id,
            )
            .order_by(Share.created_at.desc(), Share.id.desc())
            .limit(1)
        )
        if row is None:
            row = Share(
                owner_id=user.id, kind=body.kind, conversation_id=body.conversation_id, file_id=body.file_id, audience=body.audience
            )
            snapshot(db, row)
            db.add(row)
            db.flush()
        else:
            # POST updates the audience but only an explicit refresh advances the snapshot.
            probe = Share(owner_id=user.id, kind=body.kind, conversation_id=body.conversation_id, file_id=body.file_id)
            snapshot(db, probe)
            row.audience, row.updated_at = body.audience, utcnow()
        set_recipients(db, row, body.user_ids if body.audience == "people" else [])
        record_audit(db, "shares.create", "share", row.id, actor_id=user.id)
        db.commit()
        return share_out(db, row)


@router.get("/v1/shares", response_model=ShareList)
def listing(
    scope: Literal["mine", "received"] = "mine",
    cursor: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    q = select(Share).where(
        *live_conditions(), Share.owner_id == user.id if scope == "mine" else and_(Share.owner_id != user.id, received(user.id))
    )
    if cursor:
        anchor = authorized(db, cursor, user)
        q = q.where(or_(Share.created_at < anchor.created_at, and_(Share.created_at == anchor.created_at, Share.id < anchor.id)))
    rows = list(db.scalars(q.order_by(Share.created_at.desc(), Share.id.desc()).limit(limit + 1)))
    return {"items": [share_out(db, r) for r in rows[:limit]], "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


@router.get("/v1/shares/for", response_model=list[ShareOut])
def for_object(
    response: Response,
    conversation_id: str | None = None,
    file_id: str | None = None,
    cursor: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=50, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if bool(conversation_id) == bool(file_id):
        raise HTTPException(422, "exactly one target required")
    q = select(Share).where(
        *live_conditions(),
        Share.owner_id == user.id,
        Share.conversation_id == conversation_id if conversation_id else Share.file_id == file_id,
    )
    if cursor:
        anchor = authorized(db, cursor, user, True)
        q = q.where(or_(Share.created_at < anchor.created_at, and_(Share.created_at == anchor.created_at, Share.id < anchor.id)))
    rows = list(db.scalars(q.order_by(Share.created_at.desc(), Share.id.desc()).limit(limit + 1)))
    if len(rows) > limit:
        response.headers["X-Next-Cursor"] = rows[limit - 1].id
    return [share_out(db, row) for row in rows[:limit]]


@router.patch("/v1/shares/{share_id}", response_model=ShareOut)
def patch(share_id: str, body: PatchShare, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with sharing_locks(db, user.id, body.user_ids):
        row = authorized(db, share_id, user, True)
        if body.refresh:
            snapshot(db, row)
        if body.audience is not None:
            row.audience = body.audience
        if row.audience == "everyone" or body.user_ids is not None:
            set_recipients(db, row, body.user_ids or [] if row.audience == "people" else [])
        row.updated_at = utcnow()
        record_audit(db, "shares.update", "share", row.id, actor_id=user.id)
        db.commit()
        return share_out(db, row)


@router.delete("/v1/shares/{share_id}", status_code=204)
def revoke(share_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        row = authorized(db, share_id, user, True)
        row.revoked_at = row.updated_at = utcnow()
        record_audit(db, "shares.revoke", "share", row.id, actor_id=user.id)
        db.commit()
    return Response(status_code=204)


def share_file(row, file):
    base = f"/api/v1/shares/{row.id}/files/{file.id}"
    return {
        "id": file.id,
        "file_id": file.id,
        "filename": file.filename,
        "content_type": file.content_type,
        "size": file.size,
        "content_url": base + "/content",
        "download_url": base + "/content",
        "preview_url": base + "/preview",
        "thumbnail_url": base + "/thumbnail",
    }


@router.get("/v1/shares/{share_id}/view", response_model=ShareView, response_model_exclude_unset=True)
def view(
    share_id: str,
    response: Response,
    cursor: str | None = Query(default=None, max_length=40),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    row = authorized(db, share_id, user)
    response.headers["Cache-Control"] = "private, no-store"
    ids = path_ids(db, row.conversation_id, row.through_run_id) if row.kind == "conversation" else []
    if cursor and cursor not in ids:
        raise HTTPException(422, "invalid cursor")
    start = ids.index(cursor) + 1 if cursor else 0
    page = ids[start : start + 20]
    runs = {r.id: r for r in db.scalars(select(Run).where(Run.id.in_(page), Run.user_id == row.owner_id))}
    artifacts = {}
    for rid, file in db.execute(
        select(Artifact.run_id, FileObject)
        .join(FileObject, FileObject.id == Artifact.file_id)
        .where(
            Artifact.run_id.in_(page),
            FileObject.user_id == row.owner_id,
            FileObject.deleted_at.is_(None),
            FileObject.purged_at.is_(None),
            FileObject.status == FileStatus.READY,
        )
    ):
        artifacts.setdefault(rid, []).append(share_file(row, file))
    attachment_ids = {fid for r in runs.values() for fid in r.input_file_ids}
    names = dict(
        db.execute(
            select(FileObject.id, FileObject.filename).where(FileObject.id.in_(attachment_ids), FileObject.user_id == row.owner_id)
        ).all()
    )
    owner = db.get(User, row.owner_id)
    result = {
        "share": share_out(db, row),
        "owner": {"display_name": owner.display_name},
        "title": row.title,
        "turns": [
            {
                "id": r.id,
                "prompt": r.prompt,
                "answer": r.summary,
                "created_at": r.created_at,
                "mode": (r.options or {}).get("mode", "work"),
                "artifacts": artifacts.get(r.id, []),
                "attachments": [{"filename": names[fid]} for fid in r.input_file_ids if fid in names],
            }
            for rid in page
            if (r := runs.get(rid))
        ],
        "next_cursor": page[-1] if start + 20 < len(ids) else None,
    }
    if row.kind == "file":
        result["file"] = share_file(row, db.get(FileObject, row.file_id))
    return result


def bound_file(db, row, file_id):
    file = db.get(FileObject, file_id)
    if not file or file.user_id != row.owner_id or file.deleted_at or file.purged_at or file.status != FileStatus.READY:
        raise HTTPException(404, "file not found")
    if row.kind == "file":
        allowed = file.id == row.file_id
    else:
        ids = path_ids(db, row.conversation_id, row.through_run_id)
        allowed = db.scalar(select(Artifact.id).where(Artifact.run_id.in_(ids), Artifact.file_id == file_id).limit(1))
    if not allowed:
        raise HTTPException(404, "file not found")
    return file


@router.get("/v1/shares/{share_id}/files/{file_id}/{action}")
@router.get("/v1/shares/{share_id}/files/{file_id}/preview/{name}")
def file_content(
    share_id: str,
    file_id: str,
    response: Response,
    action: str = "preview",
    name: str | None = None,
    if_none_match: str | None = Header(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    row = authorized(db, share_id, user)
    bound_file(db, row, file_id)
    owner = db.get(User, row.owner_id)
    # Reuse the existing storage streams, manifest creation, path allowlists and ETags after share authorization.
    if name is not None:
        result = routes_files.preview_asset(file_id, name, if_none_match, owner, db)
    elif action == "content":
        result = routes_files.download_file(file_id, owner, db)
    elif action == "thumbnail":
        result = routes_files.download_thumbnail(file_id, if_none_match, owner, db)
    elif action == "preview":
        response.headers["Cache-Control"] = "private, no-store"
        return routes_files.get_preview(file_id, owner, db)
    else:
        raise HTTPException(404, "not found")
    result.headers["Cache-Control"] = "private, no-store"
    return result
