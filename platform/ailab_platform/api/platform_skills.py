"""API-owned platform skill versions, subscription resolution and run authorization."""

from fastapi import HTTPException
from sqlalchemy import select

from ..agent.prompts import skills
from ..models import FileObject, FileStatus, PlatformSkill, SkillApproval, UserPlatformSkill
from ..skill_policy import effective_policy

SYSTEM_OWNER = "sys_platform_skills"


def active_skill(db, skill_id):
    row = db.get(PlatformSkill, skill_id, populate_existing=True)
    if not row or row.deleted_at:
        raise HTTPException(404, "平台技能不存在")
    return row


def subscription(db, user_id, skill_id):
    row = db.get(UserPlatformSkill, (user_id, skill_id), populate_existing=True)
    if not row:
        raise HTTPException(404, "尚未添加平台技能")
    return row


def platform_name(db, name):
    return db.scalar(select(PlatformSkill).where(PlatformSkill.name == name, PlatformSkill.deleted_at.is_(None)))


def check_name(db, name, *, lineage_id=None):
    """A customized copy may retain its own platform name, but cannot claim another platform name."""
    row = platform_name(db, name)
    if row and not (
        lineage_id
        and db.scalar(
            select(UserPlatformSkill.user_id)
            .where(UserPlatformSkill.skill_id == row.id, UserPlatformSkill.custom_lineage_id == lineage_id)
            .limit(1)
        )
    ):
        raise HTTPException(422, "与平台技能重名，请通过自定义草稿编辑")


def subscriptions_query(user_id):
    return (
        select(PlatformSkill, UserPlatformSkill)
        .join(UserPlatformSkill, UserPlatformSkill.skill_id == PlatformSkill.id)
        .where(UserPlatformSkill.user_id == user_id, PlatformSkill.deleted_at.is_(None))
    )


def added_skills(db, user):
    rows = list(db.execute(subscriptions_query(user.id)))
    ids = [s.current_file_id for s, sub in rows if not sub.custom_lineage_id and s.current_file_id]
    lineages = [sub.custom_lineage_id for s, sub in rows if sub.custom_lineage_id]
    files = (
        list(
            db.scalars(
                select(FileObject).where(
                    (FileObject.id.in_(ids)) | (FileObject.lineage_id.in_(lineages) & (FileObject.is_head == 1)),
                    FileObject.deleted_at.is_(None),
                    FileObject.purged_at.is_(None),
                )
            )
        )
        if rows
        else []
    )
    by_id, by_lineage = {f.id: f for f in files}, {f.lineage_id: f for f in files if f.is_head}
    result = []
    for skill, sub in rows:
        file = by_lineage.get(sub.custom_lineage_id) if sub.custom_lineage_id else by_id.get(skill.current_file_id)
        if not file:
            continue
        meta = (file.meta or {}).get("skill") or {}
        result.append(
            {
                **meta,
                "id": skill.id,
                "name": skill.name,
                "title": (meta.get("title") or skill.title) if sub.custom_lineage_id else skill.title,
                "description": meta.get("description", skill.description) if sub.custom_lineage_id else skill.description,
                "file_id": file.id,
                "filename": file.filename,
                "lineage_id": file.lineage_id,
                "kind": meta.get("kind", "md"),
                "platform": True,
                "added": True,
                "customized": bool(sub.custom_lineage_id),
                "update_available": bool(sub.custom_lineage_id and sub.base_file_id != skill.current_file_id),
                "error": (file.meta or {}).get("skill_error")
                or (None if file.status == FileStatus.READY and meta else "安全扫描中或技能不可用"),
            }
        )
    return result


def usable_platform(db, user):
    denied, policy = effective_policy(db, user)
    off = denied | set((user.settings or {}).get("disabled_skills") or [])
    items = added_skills(db, user)
    approvals = approval_states(db, items)
    out = {}
    for item in items:
        if item["error"] or item["name"] in off:
            continue
        if item["customized"]:
            state = approvals.get(item["file_id"])
            if policy == "disabled" or state == "disabled" or (policy == "review" and state != "allowed"):
                continue
        out[item["name"]] = item
    return out


def frozen_platform(db, user, run):
    """Current permission, immutable run identity. Never substitute the latest published version."""
    live = {s.id: sub for s, sub in db.execute(subscriptions_query(user.id))}
    denied, policy = effective_policy(db, user)
    off = denied | set((user.settings or {}).get("disabled_skills") or [])
    items = (run.runtime_meta or {}).get("platform_skills", [])
    approvals = approval_states(db, items)
    result = []
    for item in items:
        sub = live.get(item["id"])
        if not sub or item["name"] in off:
            continue
        if item["customized"]:
            if item["lineage_id"] != sub.custom_lineage_id:
                continue
            state = approvals.get(item["file_id"])
            if policy == "disabled" or state == "disabled" or (policy == "review" and state != "allowed"):
                continue
        result.append(item)
    return result


def activate(db, file):
    if file.purpose != "platform_skill" or file.status != FileStatus.READY:
        return
    row = db.get(PlatformSkill, (file.meta or {}).get("platform_skill_id"), populate_existing=True)
    meta = file.meta or {}
    if not row or row.deleted_at or not file.is_head or not meta.get("skill") or meta.get("skill_error") or meta.get("platform_published"):
        return
    if meta["skill"].get("name") != row.name or file.user_id != SYSTEM_OWNER:
        return
    row.current_file_id = file.id
    file.meta = {**meta, "platform_published": True}
    from ..library import trim_file_versions

    trim_file_versions(db, file)


def assert_available_name(db, name):
    if name in skills() or platform_name(db, name):
        raise HTTPException(422, "技能名称已被占用")
    # Pending writes reserve their declared name too; untrusted uploads are checked when the Worker identifies them.
    name_expr = FileObject.meta["skill"]["name"].as_string()
    claim_expr = FileObject.meta["skill_claim"].as_string()
    if db.scalar(
        select(FileObject.id)
        .where(
            FileObject.purpose == "skill",
            FileObject.is_head == 1,
            FileObject.deleted_at.is_(None),
            FileObject.purged_at.is_(None),
            (name_expr == name) | (claim_expr == name),
        )
        .limit(1)
    ):
        raise HTTPException(422, "与个人技能重名")


def draft_source_allowed(db, draft, file):
    if not file or file.deleted_at or file.purged_at or file.status != FileStatus.READY:
        return False
    if draft.origin == "platform_admin":
        skill = db.get(PlatformSkill, draft.target, populate_existing=True)
        return bool(
            skill
            and not skill.deleted_at
            and file.user_id == SYSTEM_OWNER
            and file.purpose == "platform_skill"
            and (file.meta or {}).get("platform_skill_id") == skill.id
            and (file.meta or {}).get("platform_published")
        )
    if draft.origin != "platform":
        return file.user_id == draft.user_id and bool(file.is_head)
    skill = db.get(PlatformSkill, draft.target, populate_existing=True)
    sub = db.get(UserPlatformSkill, (draft.user_id, draft.target), populate_existing=True)
    if not skill or skill.deleted_at or not sub:
        return False
    if file.purpose == "platform_skill":
        return (
            file.user_id == SYSTEM_OWNER
            and (file.meta or {}).get("platform_skill_id") == skill.id
            and bool((file.meta or {}).get("platform_published"))
        )
    return file.user_id == draft.user_id and bool(file.is_head) and file.lineage_id == sub.custom_lineage_id


def approval_states(db, items):
    ids = [s["file_id"] for s in items if s.get("customized")]
    return {row.file_id: row.state for row in db.scalars(select(SkillApproval).where(SkillApproval.file_id.in_(ids)))} if ids else {}
