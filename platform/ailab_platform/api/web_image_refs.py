"""Small address records, never image bytes. Only the API writes these records."""

import hashlib
import re
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, exists, select, update

from .. import web
from ..models import Conversation, Run, RunStatus, User, WebImage, WebImageRef, utcnow

ID = re.compile(r"(?<![A-Za-z0-9_-])img_[A-Za-z0-9_-]{12}(?![A-Za-z0-9_-])")
CANDIDATE_TTL = timedelta(hours=24)


def _insert(db, model):
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(model)


def register(db, run, images):
    now = utcnow()
    result = []
    for ordinal, item in sorted(
        enumerate(images), key=lambda pair: hashlib.sha256((web.image_reference(pair[1].image) or "").encode()).hexdigest()
    ):
        url = web.image_reference(item.image)
        if not url:
            continue
        digest = hashlib.sha256(url.encode()).hexdigest()
        db.execute(
            _insert(db, WebImage)
            .values(
                id="img_" + secrets.token_urlsafe(9),
                url_hash=digest,
                url=url,
                thumbnail_url=web.image_reference(item.thumbnail_url) or "",
                page_url=item.page_url,
                title=item.title or item.alt,
                expires_at=now + CANDIDATE_TTL,
            )
            .on_conflict_do_update(index_elements=["url_hash"], set_={"url_hash": digest})
        )
        # Serialize against collection and other users registering the same URL.
        image = db.scalar(select(WebImage).where(WebImage.url_hash == digest).with_for_update())
        if not image.thumbnail_url:
            image.thumbnail_url = web.image_reference(item.thumbnail_url) or ""
        if not image.page_url:
            image.page_url = item.page_url
        if image.expires_at is not None:
            image.expires_at = now + CANDIDATE_TTL
        db.execute(
            _insert(db, WebImageRef)
            .values(run_id=run.id, image_id=image.id, expires_at=now + CANDIDATE_TTL)
            .on_conflict_do_nothing(index_elements=["run_id", "image_id"])
        )
        result.append(
            (
                ordinal,
                {
                    "image": image.id,
                    "title": item.title or item.alt,
                    "page_url": item.page_url,
                    **({"width": item.width, "height": item.height} if item.width and item.height else {}),
                },
            )
        )
    return [item for _, item in sorted(result)]


def retain(db, run, text):
    ids = set(ID.findall(text or ""))
    if not ids:
        return
    allowed = (
        select(WebImageRef.image_id)
        .join(Run, Run.id == WebImageRef.run_id)
        .where(
            WebImageRef.image_id.in_(ids),
            Run.user_id == run.user_id,
            Run.conversation_id == run.conversation_id if run.conversation_id else Run.id == run.id,
        )
        .distinct()
    )
    for image_id in db.scalars(select(WebImage.id).where(WebImage.id.in_(allowed)).order_by(WebImage.url_hash)).all():
        # The image lock orders pinning against metadata collection.
        if db.scalar(select(WebImage.id).where(WebImage.id == image_id).with_for_update()) is None:
            continue
        db.execute(
            _insert(db, WebImageRef)
            .values(run_id=run.id, image_id=image_id, expires_at=None)
            .on_conflict_do_update(index_elements=["run_id", "image_id"], set_={"expires_at": None})
        )
        db.execute(update(WebImage).where(WebImage.id == image_id).values(expires_at=None))


def copy_refs(db, old, new):
    for ref in db.scalars(select(WebImageRef).where(WebImageRef.run_id == old.id, WebImageRef.expires_at.is_(None))):
        db.add(WebImageRef(run_id=new.id, image_id=ref.image_id, expires_at=None))


def release_runs(db, ids):
    images = select(WebImageRef.image_id).where(WebImageRef.run_id.in_(ids))
    # Schedule a later orphan check before deleting refs, in the same transaction.
    for image_id in db.scalars(select(WebImage.id).where(WebImage.id.in_(images)).order_by(WebImage.url_hash)).all():
        db.execute(update(WebImage).where(WebImage.id == image_id).values(expires_at=utcnow()))
    db.execute(delete(WebImageRef).where(WebImageRef.run_id.in_(ids)))


def collect(db, limit):
    now = utcnow()
    refs = db.execute(
        select(WebImageRef.run_id, WebImageRef.image_id, Run.status)
        .join(Run, Run.id == WebImageRef.run_id)
        .where(WebImageRef.expires_at < now)
        .order_by(WebImageRef.expires_at, WebImageRef.run_id, WebImageRef.image_id)
        .limit(limit)
    ).all()
    for run_id, image_id, status in sorted(refs, key=lambda ref: ref.image_id):
        locked = db.scalar(select(WebImage.id).where(WebImage.id == image_id).with_for_update(skip_locked=True))
        if locked is None:
            continue
        expired = (WebImageRef.run_id == run_id, WebImageRef.image_id == image_id, WebImageRef.expires_at < now)
        if status in RunStatus.ACTIVE:
            db.execute(update(WebImageRef).where(*expired).values(expires_at=now + CANDIDATE_TTL))
        else:
            db.execute(delete(WebImageRef).where(*expired))
            db.execute(update(WebImage).where(WebImage.id == image_id, WebImage.expires_at.is_(None)).values(expires_at=now))
    images = db.scalars(
        select(WebImage)
        .where(WebImage.expires_at < now)
        .order_by(WebImage.expires_at, WebImage.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for image in images:
        retained = db.scalar(select(WebImageRef.image_id).where(WebImageRef.image_id == image.id).limit(1))
        if retained:
            pinned = db.scalar(
                select(WebImageRef.image_id).where(WebImageRef.image_id == image.id, WebImageRef.expires_at.is_(None)).limit(1)
            )
            image.expires_at = None if pinned else now + CANDIDATE_TTL
        else:
            db.delete(image)
    return len(images)


def authorized(db, image_id, user, share_id=None):
    if not ID.fullmatch(image_id):
        raise HTTPException(404, "image not found")
    query = select(WebImage).join(WebImageRef, WebImageRef.image_id == WebImage.id).join(Run, Run.id == WebImageRef.run_id)
    if share_id:
        from ..conversation_tree import path_ids
        from .routes_shares import authorized as share_authorized

        share = share_authorized(db, share_id, user)
        if share.kind != "conversation":
            raise HTTPException(404, "image not found")
        query = query.where(Run.id.in_(path_ids(db, share.conversation_id, share.through_run_id)), WebImageRef.expires_at.is_(None))
    else:
        live = exists(select(Conversation.id).where(Conversation.id == Run.conversation_id, Conversation.deleted_at.is_(None)))
        query = query.where(Run.user_id == user.id, (Run.conversation_id.is_(None) | live))
    query = query.where(
        (WebImageRef.expires_at.is_(None) | (WebImageRef.expires_at > utcnow()) | Run.status.in_(RunStatus.ACTIVE)),
        WebImage.id == image_id,
        exists(select(User.id).where(User.id == Run.user_id, User.disabled_at.is_(None))),
    )
    row = db.scalar(query.limit(1))
    if row is None:
        raise HTTPException(404, "image not found")
    return {"url": row.url, "thumbnail_url": row.thumbnail_url, "page_url": row.page_url, "title": row.title}
