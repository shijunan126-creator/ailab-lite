"""API-owned bytes: reserve before I/O, then bind metadata and publish together."""

import hashlib
import logging
from pathlib import Path

from fastapi import HTTPException

from ..locks import user_critical_section
from ..models import DerivedAsset
from ..storage import get_storage
from . import derived_assets as assets

log = logging.getLogger("ailab.api.owned_assets")


def publish(db, owner, key, payload, version, *, kind, validate, bind, content_type, reconstructible=True):
    """Validators execute inside the owner's lock twice; storage never holds a DB transaction."""
    if isinstance(payload, Path):
        size = payload.stat().st_size
        with payload.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    else:
        size, digest = len(payload), hashlib.sha256(payload).hexdigest()
    storage = get_storage()
    grant, succeeded = None, False
    try:
        with user_critical_section(db, owner):
            validate()
            row = db.get(DerivedAsset, key, populate_existing=True)
            reusable = bool(
                row and row.state == "ready" and row.source_version == version and row.digest == digest and row.size_bytes == size
            )
            db.rollback()
        if reusable:
            # Only identical canonical bytes can be rebound without a reservation.
            # A missing object is an explicit cache error, not an empty success.
            verified, length = hashlib.sha256(), 0
            with storage.open_stream(key) as chunks:
                for chunk in chunks:
                    length += len(chunk)
                    if length > size:
                        raise HTTPException(409, "derived_cache_changed")
                    verified.update(chunk)
            if length != size or verified.hexdigest() != digest:
                raise HTTPException(409, "derived_cache_changed")
            with user_critical_section(db, owner):
                validate()
                row = db.get(DerivedAsset, key, populate_existing=True)
                if not row or row.state != "ready" or row.digest != digest or row.source_version != version:
                    raise HTTPException(409, "derived_generation_stale")
                result = bind(key)
                db.commit()
                return result
        with user_critical_section(db, owner):
            validate()
            grant = assets.reserve(db, key, size, version, kind=kind, reconstructible=reconstructible)
            db.commit()
        if isinstance(payload, Path):
            storage.put_file(key, payload, content_type)
        elif isinstance(payload, bytearray):
            storage.put_stream(
                key, (bytes(memoryview(payload)[offset : offset + 65536]) for offset in range(0, size, 65536)), content_type, max_bytes=size
            )
        else:
            storage.put_bytes(key, payload, content_type)
        with user_critical_section(db, owner):
            validate()
            assets.finish(db, key, grant["token"], size, digest)
            result = bind(key)
            db.commit()
            succeeded = True
            return result
    finally:
        if grant and not succeeded:
            db.rollback()
            with user_critical_section(db, owner):
                row = db.get(DerivedAsset, key, populate_existing=True)
                deletable = bool(row and row.token == grant["token"] and row.state in {"writing", "staged", "deleting"})
                if deletable:
                    assets.abandon(db, key, grant["token"])
                    db.commit()
            if deletable:
                try:
                    storage.delete(key)
                    assets.forget_deleted(db, key)
                    db.commit()
                except Exception:
                    db.rollback()
                    log.warning("API asset cleanup remains charged for compensation", exc_info=True)
