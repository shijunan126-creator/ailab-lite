"""Recover only verifiable legacy provenance. Dry-run by default; never merges or deletes files.

Run from platform: python scripts/repair_file_identity.py [--apply] [--after FILE_ID] [--limit 200]
Each bounded batch prints its resume cursor. No business names/content are emitted.
"""

import argparse
import json

from sqlalchemy import select

from ailab_platform.api.artifact_identity import repair_metadata
from ailab_platform.db import SessionLocal
from ailab_platform.locks import user_critical_section
from ailab_platform.models import Artifact, FileObject


def batch(db, *, after="", limit=200, apply=False):
    ids = list(
        db.scalars(
            select(FileObject.id)
            .where(
                FileObject.id > after,
                select(Artifact.id).where(Artifact.file_id == FileObject.id).exists(),
            )
            .order_by(FileObject.id)
            .limit(limit)
        )
    )
    repaired, unresolved = [], []
    for file_id in ids:
        file = db.get(FileObject, file_id)
        with user_critical_section(db, file.user_id):
            db.refresh(file)
            if (file.meta or {}).get("artifact_filename"):
                continue
            if repair_metadata(db, file):
                repaired.append(file_id)
            else:
                unresolved.append(file_id)
            if apply:
                db.commit()
            else:
                db.rollback()
    return {"apply": apply, "scanned": len(ids), "repaired": repaired, "unresolved": unresolved, "after": ids[-1] if ids else after}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--after", default="")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        parser.error("limit must be between 1 and 500")
    with SessionLocal() as db:
        print(json.dumps(batch(db, **vars(args))))
