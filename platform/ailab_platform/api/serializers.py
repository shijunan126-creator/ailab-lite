"""Response serialization helpers (kept out of route modules for reuse)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from ..models import Artifact, FileObject, ModelUsage, QueuedMessage, Run


def file_dict(file: FileObject, versions: int | None = None) -> dict:
    if versions is None:
        db = object_session(file)
        versions = (
            db.scalar(
                select(func.count()).select_from(FileObject).where(FileObject.lineage_id == file.lineage_id, FileObject.purged_at.is_(None))
            )
            if db
            else int(file.purged_at is None)
        )
    return {
        "id": file.id,
        "project_id": file.project_id,
        "starred": file.starred_at is not None,
        "filename": file.filename,
        "content_type": file.content_type,
        "size": file.size,
        "sha256": file.sha256,
        "status": file.status,
        "version": file.version,
        "lineage_id": file.lineage_id,
        "versions": versions,
        "is_head": bool(file.is_head),
        "deleted_at": file.deleted_at,
        "purge_at": file.purge_at,
        "purged": file.purged_at is not None,
        "trashed_from_project_id": file.trashed_from_project_id,
        "meta": file.meta or {},
        "created_at": file.created_at,
        "download_url": f"/v1/files/{file.id}/content",
        # a purged version keeps its row, not its derived objects
        "preview": (file.meta or {}).get("preview") if file.purged_at is None else None,
        "thumbnail_url": f"/v1/files/{file.id}/thumbnail" if file.purged_at is None and (file.meta or {}).get("thumb_key") else None,
    }


def artifact_dict(artifact: Artifact, file: FileObject) -> dict:
    return {
        "id": artifact.id,
        "file_id": file.id,
        "role": artifact.role,
        "filename": file.filename,
        "content_type": file.content_type,
        "size": file.size,
        "sha256": file.sha256,
        "download_url": f"/v1/files/{file.id}/content",
    }


def run_dict(db: Session, run: Run, detail: bool = False) -> dict:
    if detail:
        return run_dicts(db, [run])[0]
    out = {
        "id": run.id,
        "retry_of": run.retry_of,
        "edited_from": run.edited_from,
        "parent_run_id": run.parent_run_id,
        "superseded_by_run_id": run.superseded_by_run_id,
        "user_id": run.user_id,
        "conversation_id": run.conversation_id,
        "title": run.title,
        "prompt": run.prompt,
        "status": run.status,
        "model": run.model,
        "input_file_ids": run.input_file_ids or [],
        "options": run.options or {},
        "template": {k: run.options["template"][k] for k in ("name", "source")} if (run.options or {}).get("template") else None,
        "summary": run.summary,
        "error": run.error,
        "result": {**(run.result or {}), **{k: v for k, v in (run.runtime_meta or {}).items() if k in {"plan", "question"}}},
        "resumable": run.status == "interrupted"
        and run.checkpoint_step > 0
        and not ((run.runtime_meta or {}).get("question") and not (run.runtime_meta or {}).get("answer")),
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "work_ms": run.work_ms,
    }
    return out


def run_dicts(db: Session, runs: list[Run]) -> list[dict]:
    """Serialize one bounded page with four related-row reads, regardless of turn count."""
    if not runs:
        return []
    ids = [run.id for run in runs]
    artifacts = {}
    for artifact, file in db.execute(
        select(Artifact, FileObject).join(FileObject, Artifact.file_id == FileObject.id).where(Artifact.run_id.in_(ids))
    ):
        artifacts.setdefault(artifact.run_id, []).append(artifact_dict(artifact, file))
    file_ids = {fid for run in runs for fid in run.input_file_ids or []}
    files = dict(db.execute(select(FileObject.id, FileObject.filename).where(FileObject.id.in_(file_ids))).all()) if file_ids else {}
    applied = {run.id: (run.runtime_meta or {}).get("steer_ids", []) for run in runs}
    steer_ids = {qid for values in applied.values() for qid in values}
    prompts = (
        {
            (rid, qid): prompt
            for rid, qid, prompt in db.execute(
                select(QueuedMessage.run_id, QueuedMessage.id, QueuedMessage.prompt).where(
                    QueuedMessage.run_id.in_(ids), QueuedMessage.id.in_(steer_ids), QueuedMessage.state == "steered"
                )
            )
        }
        if steer_ids
        else {}
    )
    columns = ("prompt_tokens", "completion_tokens", "cached_tokens")
    usage = {
        row[0]: dict(zip((*columns, "calls"), row[1:], strict=True))
        for row in db.execute(
            select(ModelUsage.run_id, *(func.sum(getattr(ModelUsage, name)) for name in columns), func.count(ModelUsage.id))
            .where(ModelUsage.run_id.in_(ids))
            .group_by(ModelUsage.run_id)
        )
    }
    result = []
    for run in runs:
        out = run_dict(db, run)
        out["steered"] = [{"id": qid, "prompt": prompts[(run.id, qid)]} for qid in applied[run.id] if (run.id, qid) in prompts]
        out["artifacts"] = artifacts.get(run.id, [])
        out["input_files"] = [{"file_id": fid, "filename": files[fid]} for fid in run.input_file_ids or [] if fid in files]
        out["usage"] = (run.result or {}).get("forked_usage") or usage.get(run.id, dict.fromkeys((*columns, "calls"), 0))
        result.append(out)
    return result


def file_dicts(db: Session, files: list[FileObject]) -> list[dict]:
    if not files:
        return []
    counts = dict(
        db.execute(
            select(FileObject.lineage_id, func.count())
            .where(FileObject.lineage_id.in_({f.lineage_id for f in files}), FileObject.purged_at.is_(None))
            .group_by(FileObject.lineage_id)
        ).all()
    )
    return [file_dict(file, counts.get(file.lineage_id, 0)) for file in files]
