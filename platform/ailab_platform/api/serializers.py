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
    if detail:
        applied = (run.runtime_meta or {}).get("steer_ids", [])[:5]
        prompts = (
            dict(
                db.execute(
                    select(QueuedMessage.id, QueuedMessage.prompt)
                    .where(QueuedMessage.run_id == run.id, QueuedMessage.state == "steered", QueuedMessage.id.in_(applied))
                    .limit(5)
                ).all()
            )
            if applied
            else {}
        )
        out["steered"] = [{"id": qid, "prompt": prompts[qid]} for qid in applied if qid in prompts]
        rows = db.execute(
            select(Artifact, FileObject).join(FileObject, Artifact.file_id == FileObject.id).where(Artifact.run_id == run.id)
        ).all()
        out["artifacts"] = [artifact_dict(a, f) for a, f in rows]
        out["input_files"] = [
            {"file_id": f.id, "filename": f.filename}
            for f in db.scalars(select(FileObject).where(FileObject.id.in_(run.input_file_ids or [])))
        ]
        usage = db.execute(select(ModelUsage).where(ModelUsage.run_id == run.id)).scalars().all()
        out["usage"] = (run.result or {}).get("forked_usage") or {
            "prompt_tokens": sum(u.prompt_tokens for u in usage),
            "completion_tokens": sum(u.completion_tokens for u in usage),
            "cached_tokens": sum(u.cached_tokens for u in usage),
            "calls": len(usage),
        }
    return out


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
