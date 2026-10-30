"""Worker-only, lease-fenced draft unpack protocol."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import get_db
from ..locks import user_critical_section
from ..models import FileObject, OutboxMessage, SkillDraft
from ..service_auth import require_scope
from ..skill_drafts.files import checked_path, update_metadata, validate_manifest
from ..skillfiles import SkillFileError
from ..storage import get_storage

router = APIRouter(prefix="/internal/skill-drafts", dependencies=[Depends(require_scope("jobs:file"))])


class Claim(BaseModel):
    job_id: str
    token: str


class Completion(Claim):
    files: list[dict] = Field(default_factory=list, max_length=200)
    error: str | None = Field(default=None, max_length=500)


def fenced(db, draft_id, body):
    draft = db.get(SkillDraft, draft_id, populate_existing=True)
    job = db.get(OutboxMessage, body.job_id, populate_existing=True)
    now = datetime.now(UTC)
    if (
        not draft
        or not job
        or job.payload.get("kind") != "skill_unpack"
        or job.payload.get("draft_id") != draft_id
        or draft.claim_token != body.job_id
        or job.claim_token != body.token
        or not job.claimed_until
        or job.claimed_until.replace(tzinfo=UTC) <= now
        or draft.status != "preparing"
    ):
        raise HTTPException(409, "stale_claim")
    file = db.get(FileObject, draft.base_file_id, populate_existing=True)
    if not file or file.user_id != draft.user_id or not file.is_head or file.deleted_at or file.purged_at:
        raise HTTPException(409, "技能已被修改")
    return draft, file


@router.post("/{draft_id}/source")
def source(draft_id: str, body: Claim, db: Session = Depends(get_db)):
    draft, file = fenced(db, draft_id, body)
    return {"user_id": draft.user_id, "storage_key": file.storage_key, "filename": file.filename}


@router.post("/{draft_id}/complete")
def complete(draft_id: str, body: Completion, db: Session = Depends(get_db)):
    draft = db.get(SkillDraft, draft_id)
    if not draft:
        raise HTTPException(404, "草稿不存在")
    with user_critical_section(db, draft.user_id):
        draft, _ = fenced(db, draft_id, body)
        if body.error:
            draft.status, draft.stage, draft.error = "failed", None, "技能解包失败，请重试"
            draft.claim_token = None
        else:
            prefix = f"skill-drafts/{draft.user_id}/{draft.id}/"
            for item in body.files:
                try:
                    checked_path(item["path"])
                    digest = item["sha256"]
                    if (
                        len(digest) != 64
                        or any(c not in "0123456789abcdef" for c in digest)
                        or item["blob"] != prefix + digest
                        or item["kind"] not in ("text", "image", "binary")
                        or item["change"] != "unchanged"
                        or get_storage().size(item["blob"]) != item["size"]
                    ):
                        raise ValueError()
                except (KeyError, ValueError, OSError) as exc:
                    raise HTTPException(422, "无效的文件清单") from exc
                item["baseline"] = digest
            overlay = {f["path"]: f for f in draft.files or []}
            merged = []
            for item in body.files:
                replacement = overlay.pop(item["path"], None)
                if replacement:
                    replacement["baseline"] = item["sha256"]
                    if replacement["change"] != "deleted":
                        replacement["change"] = "unchanged" if replacement["sha256"] == item["sha256"] else "modified"
                    merged.append(replacement)
                else:
                    merged.append(item)
            merged.extend(f for f in overlay.values() if f["change"] != "deleted")
            try:
                validate_manifest(merged, require_skill=True)
                from .. import skillfiles

                md = next(f for f in merged if f["path"] == "SKILL.md" and f["change"] != "deleted")
                if not skillfiles.inspect(get_storage().get_bytes(md["blob"]), "SKILL.md"):
                    raise SkillFileError("无效的技能说明")
            except (SkillFileError, KeyError):
                draft.status, draft.stage, draft.error = "failed", None, "技能说明无效，请重试"
                if draft.origin == "conversation":
                    from .inbox import create_notification

                    create_notification(db, draft.user_id, "skill_draft.failed", "技能提取失败", draft.conversation_id)
                db.commit()
                return {"ok": False}
            draft.files = merged
            update_metadata(draft, get_storage())
            draft.claim_token = None
            if draft.origin == "conversation":
                draft.status, draft.stage = "queued", "reading"
            else:
                draft.status, draft.stage = "ready", None
            draft.revision += 1
        if draft.status == "failed" and draft.origin == "conversation":
            from .inbox import create_notification

            create_notification(db, draft.user_id, "skill_draft.failed", "技能提取失败", draft.conversation_id)
        db.commit()
    return {"ok": True}
