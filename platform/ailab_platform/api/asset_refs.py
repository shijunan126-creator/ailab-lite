"""Frozen assets are retained independently of message-source invalidation."""

from sqlalchemy import select

from ..models import RunAssetRef


def register_assets(db, run):
    assets = set()
    template = (run.options or {}).get("template") or {}
    if template.get("file_id"):
        assets.add((template["file_id"], "template"))
    for fid in (((run.runtime_meta or {}).get("brand") or {}).get("assets") or {}).values():
        if fid:
            assets.add((fid, "brand"))
    for skill in (run.runtime_meta or {}).get("platform_skills", []):
        assets.add((skill["file_id"], "platform_skill"))
    for fid, kind in assets:
        register_asset(db, run.id, fid, kind)


def register_asset(db, run_id, file_id, kind):
    if any(isinstance(r, RunAssetRef) and (r.run_id, r.file_id, r.kind) == (run_id, file_id, kind) for r in db.new):
        return
    if (
        db.scalar(select(RunAssetRef.id).where(RunAssetRef.run_id == run_id, RunAssetRef.file_id == file_id, RunAssetRef.kind == kind))
        is None
    ):
        db.add(RunAssetRef(run_id=run_id, file_id=file_id, kind=kind))
