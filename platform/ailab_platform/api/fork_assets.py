"""Fork previews reserve before copying; history captures share canonical bytes."""

import logging
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from ..models import DerivedAsset, ViewerCapture, new_id
from . import derived_assets as assets

log = logging.getLogger("ailab.api.fork_assets")


class PreviewCopies:
    def __init__(self, storage, check_deadline):
        self.storage, self.check_deadline = storage, check_deadline
        self.items = []

    def prepare(self, db, turns, destinations, invalid):
        total = 0
        for old in turns:
            if old.id in invalid:
                continue
            old_id = old.id
            cursor = None
            while True:
                self.check_deadline()
                db.rollback()
                page = self.storage.list_prefix(f"previews/{old_id}/", cursor=cursor, limit=100)
                for info in page:
                    metadata = self.storage.info(info.key)
                    if metadata.size != info.size:
                        raise HTTPException(409, "fork_preview_changed")
                    total += info.size
                    if len(self.items) >= 10000 or total > 2 * 1024**3:
                        raise HTTPException(422, "分叉对象超过2GiB或10000个上限")
                    key = info.key.replace(f"previews/{old_id}/", f"previews/{destinations[old_id]}/", 1)
                    grant = assets.reserve(db, key, info.size, "fork-preview", fork_source=old_id)
                    db.commit()
                    self.items.append(
                        {**grant, "source": info.key, "run_id": destinations[old_id], "size": info.size, "digest": metadata.etag}
                    )
                cursor = page.cursor
                if cursor is None:
                    break
        db.rollback()
        # No database transaction or business lock spans storage I/O.
        for item in self.items:
            self.check_deadline()
            self.storage.copy(item["source"], item["key"])
            info = self.storage.info(item["key"])
            if info.size != item["size"] or (item["digest"] and info.etag != item["digest"]):
                raise HTTPException(409, "fork_preview_changed")
            item["digest"] = info.etag

    def publish(self, db, run):
        for item in self.items:
            if item["run_id"] == run.id:
                row = assets.finish(db, item["key"], item["token"], item["size"], item["digest"])
                row.run_id = run.id

    def cleanup(self, db):
        db.rollback()
        for item in self.items:
            try:
                assets.abandon(db, item["key"], item["token"])
                db.commit()
                self.storage.delete(item["key"])
                assets.forget_deleted(db, item["key"])
                db.commit()
            except Exception:
                db.rollback()
                log.warning("fork preview cleanup remains charged for compensation", exc_info=True)


def copy_captures(db, old, new, *, remaining=10000, check_deadline=lambda: None):
    """Fork identity changes, source version and immutable image bytes do not."""
    after, mapping = "", {}
    while True:
        check_deadline()
        rows = db.scalars(
            select(ViewerCapture).where(ViewerCapture.run_id == old.id, ViewerCapture.id > after).order_by(ViewerCapture.id).limit(100)
        ).all()
        if not rows:
            return mapping
        for row in rows:
            if len(mapping) >= remaining:
                raise HTTPException(422, "分叉对象超过10000个上限")
            asset = db.get(DerivedAsset, row.storage_key)
            if row.expires_at is not None or not asset or asset.state != "ready":
                continue  # Legacy temporary captures keep their original expiry.
            copied_id = new_id("cap")
            target = deepcopy(row.target)
            if target.get("run_id") == old.id:
                target["run_id"] = new.id
            db.add(
                ViewerCapture(
                    id=copied_id,
                    run_id=new.id,
                    file_id=row.file_id,
                    target=target,
                    storage_key=row.storage_key,
                    digest=row.digest,
                    content_type=row.content_type,
                    expires_at=None,
                )
            )
            assets.pin(db, row.storage_key, "capture:" + copied_id, kind="message", run_id=new.id)
            mapping[row.id] = copied_id
        after = rows[-1].id


def remap_captures(value, mapping):
    if isinstance(value, dict):
        return {key: remap_captures(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [remap_captures(item, mapping) for item in value]
    return mapping.get(value, value) if isinstance(value, str) else value
