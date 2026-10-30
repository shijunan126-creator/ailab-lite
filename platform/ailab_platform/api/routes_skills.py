"""Skills: 通用技能 (built in, everyone) and 个人技能 (a user's own skill files in their library).

Each user switches any of them on and off (like Claude's Customize > Skills). A skill that is off is not listed
in the model's system prompt, cannot be read with load_skill, has no button in the composer, and is refused
when a message asks for it. A personal skill is just a file: upload it to the library and the Worker recognises
it (see skillfiles.py); delete the file and the skill is gone.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text, tuple_
from sqlalchemy.orm import Session

from ..agent.prompts import skills
from ..audit import record_audit
from ..deps import current_user, get_db
from ..locks import user_critical_section
from ..models import REVIEW_PREDICATES, Department, FileObject, FileStatus, SkillApproval, SkillPolicy, User, UserRole
from ..skill_policy import effective_policy, run_disabled_skills
from ..skill_registry import BASE_SKILLS
from ..skill_registry import kind as platform_kind
from ..skillfiles import MAX_SKILL_MD

router = APIRouter(prefix="/v1/skills", tags=["skills"])


class SkillToggle(BaseModel):
    enabled: bool


# Built-in skills that other entries drive (the conversation menu's "提取为技能" runs auto-skill). They stay
# in the catalogue, the admin policy and the prompt, but the Portal does not offer them as a skill to pick.
IMPLICIT_SKILLS = frozenset({"auto-skill"})


def disabled_skills(user: User) -> list[str]:
    return list((user.settings or {}).get("disabled_skills") or [])


def personal_skills(db: Session, user: User) -> list[dict]:
    """The user's skill files, newest first; `error` says why one cannot be used (bad file, name taken)."""
    builtin = skills()
    files = db.scalars(
        select(FileObject)
        .where(
            FileObject.user_id == user.id,
            FileObject.purpose == "skill",
            FileObject.is_head == 1,
            FileObject.deleted_at.is_(None),
            FileObject.purged_at.is_(None),
        )
        .order_by(FileObject.created_at.desc())
    )
    out, seen = [], set()
    for f in files:
        meta = f.meta or {}
        item = {
            "file_id": f.id,
            "lineage_id": f.lineage_id,
            "filename": f.filename,
            "name": f"file-{f.id}",
            "created_at": f.created_at,
            **(meta.get("skill") or {}),
        }
        if f.status == FileStatus.PENDING:
            item["error"] = "安全扫描中"
        elif f.status == FileStatus.REJECTED:
            item["error"] = "未通过安全扫描"
        elif not meta.get("skill") and not meta.get("skill_error"):
            item["error"] = "不是有效的技能文件，可删除后重新上传"
        elif meta.get("skill_error"):
            item["error"] = meta["skill_error"]
        elif item["name"] in builtin:
            item["error"] = f"与通用技能 {item['name']} 重名，请在 SKILL.md 里换个 name"
        elif item["name"] in seen:
            item["error"] = "有更新的同名技能文件，这一份不再使用"
        else:
            seen.add(item["name"])
        out.append(item)
    return out


def usable_personal(db: Session, user: User) -> dict[str, dict]:
    """name -> skill for the personal skills that work and are switched on."""
    off = set(disabled_skills(user))
    _, policy = effective_policy(db, user)
    if policy == "disabled":
        return {}
    candidates = [s for s in personal_skills(db, user) if not s.get("error") and s["name"] not in off]
    ids = [s["file_id"] for s in candidates]
    approvals = {}
    for start in range(0, len(ids), 200):
        approvals.update(
            {a.file_id: a.state for a in db.scalars(select(SkillApproval).where(SkillApproval.file_id.in_(ids[start : start + 200])))}
        )
    return {
        s["name"]: s
        for s in candidates
        if approvals.get(s["file_id"]) != "disabled" and (policy == "allowed" or approvals.get(s["file_id"]) == "allowed")
    }


@router.get("/storage")
def skill_storage(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    from ..quota import library_bytes, user_limits

    used = db.scalar(
        select(func.coalesce(func.sum(FileObject.size), 0)).where(
            FileObject.user_id == user.id, FileObject.purpose == "skill", FileObject.purged_at.is_(None)
        )
    )
    limit = user_limits(db, user.id).library_bytes
    return {"used_bytes": used, "limit_bytes": limit, "available_bytes": max(0, limit - library_bytes(db, user.id))}


@router.delete("/files/{file_id}", status_code=204)
def remove_skill(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..library import delete_storage_objects, purge_lineages

    with user_critical_section(db, user.id):
        file = db.get(FileObject, file_id)
        if not file or file.user_id != user.id or file.purpose != "skill":
            raise HTTPException(404, "skill not found")
        keys = purge_lineages(db, user.id, [file.lineage_id])
        db.commit()
    delete_storage_objects(keys)


@router.get("")
def list_skills(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    off = run_disabled_skills(db, user)
    allowed = usable_personal(db, user)
    builtin = [
        {
            "name": n,
            "title": m["title"],
            "description": m["description"],
            "scope": "builtin",
            "builtin": True,
            "implicit": n in IMPLICIT_SKILLS,
            "kind": platform_kind(n),
            "toggleable": n not in BASE_SKILLS,
            "extras": ["ppt_template"] if n in {"platform.pptx", "pptx"} else [],
            "enabled": n in BASE_SKILLS or n not in off,
            **{k: m[k] for k in ("version", "sha256", "license", "requires", "roles")},
        }
        for n, m in sorted(
            skills().items(), key=lambda item: (BASE_SKILLS.index(item[0]) if item[0] in BASE_SKILLS else len(BASE_SKILLS), item[0])
        )
    ]
    personal = [
        {
            "name": s.get("name", ""),
            "title": s.get("title") or s["filename"],
            "description": s.get("description", ""),
            "scope": "personal",
            "builtin": False,
            "enabled": s.get("name") in allowed,
            "file_id": s["file_id"],
            "filename": s["filename"],
            "kind": "personal",
            "kind_file": s.get("kind"),
            "lineage_id": s.get("lineage_id"),
            "toggleable": True,
            "files": s.get("files"),
            "error": s.get("error"),
        }
        for s in personal_skills(db, user)
    ]
    return builtin + personal


@router.put("/{name}")
def toggle_skill(name: str, body: SkillToggle, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if name in BASE_SKILLS:
        raise HTTPException(403, "基础技能不能关闭")
    denied, personal_policy = effective_policy(db, user)
    builtin = skills()
    personal = {s["name"]: s for s in personal_skills(db, user) if not s.get("error")}
    if name not in builtin and name not in personal:
        raise HTTPException(status_code=404, detail=f"unknown skill: {name}")
    if body.enabled and (name in denied or (name in personal and personal_policy == "disabled")):
        raise HTTPException(403, "技能已被管理员禁用")
    known = set(builtin) | set(personal)
    with user_critical_section(db, user.id):
        db.refresh(user)  # current_user was loaded before the lock: another request may have changed settings
        off = [s for s in disabled_skills(user) if s in known and s != name]
        if not body.enabled:
            off.append(name)
        user.settings = {**(user.settings or {}), "disabled_skills": sorted(off)}  # a new dict: SQLAlchemy sees the change
        db.commit()
    return {"name": name, "enabled": body.enabled, "scope": "builtin" if name in builtin else "personal"}


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = Field(default="global", max_length=40)
    name: str = Field(max_length=64)
    state: str = Field(max_length=16)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str = Field(max_length=16)


def _admin(user):
    if "platform_admin" not in user.principal.roles:
        raise HTTPException(403, "forbidden")


@router.get("/admin/policies")
def admin_policies(after: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    scope, _, name = after.partition(":")
    if len(after) > 106:
        raise HTTPException(422, "invalid cursor")
    rows = list(
        db.scalars(
            select(SkillPolicy)
            .where(tuple_(SkillPolicy.scope, SkillPolicy.name) > (scope, name))
            .order_by(SkillPolicy.scope, SkillPolicy.name)
            .limit(101)
        )
    )
    return {
        "items": [{"scope": p.scope, "name": p.name, "state": p.state} for p in rows[:100]],
        "next_cursor": f"{rows[99].scope}:{rows[99].name}" if len(rows) > 100 else None,
    }


@router.put("/admin/policies")
def set_policy(body: PolicyInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    if body.scope != "global" and not db.get(Department, body.scope):
        raise HTTPException(422, "unknown department")
    if body.name in BASE_SKILLS:
        raise HTTPException(403, "基础技能不能关闭")
    if body.name not in skills() and body.name != "*personal*":
        raise HTTPException(422, "unknown skill")
    states = {"allowed", "review", "disabled"} if body.name == "*personal*" else {"allowed", "disabled"}
    if body.state not in states:
        raise HTTPException(422, "invalid state")
    # A stable platform-admin row serializes policy upserts across writers and API replicas.
    anchor = db.scalar(select(UserRole.user_id).where(UserRole.role == "platform_admin").order_by(UserRole.user_id).limit(1))
    with user_critical_section(db, anchor or user.id):
        row = db.get(SkillPolicy, (body.scope, body.name), populate_existing=True)
        if row:
            row.state = body.state
        else:
            db.add(SkillPolicy(**body.model_dump()))
        record_audit(db, "skills.policy", "skill", body.name, actor_id=user.id, detail={"fields": [body.scope, body.name, body.state]})
        db.commit()
    return body.model_dump()


def review_queue(after: str, limit: int, dialect: str):
    """Personal skills awaiting a decision: the owner's effective policy is review, nothing is decided yet, not removed.

    The file-id keyset walks the sparse ix_files_skill_review index; the policy checks are small EXISTS lookups.
    """
    return (
        select(FileObject)
        .join(User, User.id == FileObject.user_id)
        .outerjoin(SkillApproval, SkillApproval.file_id == FileObject.id)
        .where(
            FileObject.id > after,
            text(REVIEW_PREDICATES[dialect]),
            select(SkillPolicy.scope)
            .where(
                SkillPolicy.name == "*personal*",
                SkillPolicy.state == "review",
                (SkillPolicy.scope == "global") | (SkillPolicy.scope == User.dept_id),
            )
            .exists(),
            ~select(SkillPolicy.scope)
            .where(
                SkillPolicy.name == "*personal*",
                SkillPolicy.state == "disabled",
                (SkillPolicy.scope == "global") | (SkillPolicy.scope == User.dept_id),
            )
            .exists(),
            FileObject.status == FileStatus.READY,
            FileObject.is_head == 1,
            FileObject.deleted_at.is_(None),
            FileObject.purged_at.is_(None),
            SkillApproval.file_id.is_(None),
        )
        .order_by(FileObject.id)
        .limit(limit + 1)
    )


@router.get("/admin/reviews")
def pending_reviews(after: str = "", limit: int = 50, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    if not 1 <= limit <= 100 or len(after) > 40:
        raise HTTPException(422, "invalid page")
    # Metadata only: never disclose another user's instruction text.
    rows = db.scalars(review_queue(after, limit, db.get_bind().dialect.name)).all()
    items = [{"file_id": f.id, "name": f.meta["skill"]["name"], "owner_id": f.user_id} for f in rows[:limit]]
    return {"items": items, "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


@router.put("/admin/reviews/{file_id}")
def review_skill(file_id: str, body: ReviewInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    if body.state not in {"allowed", "disabled"}:
        raise HTTPException(422, "invalid state")
    file = db.get(FileObject, file_id)
    if not file:
        raise HTTPException(404, "not found")
    with user_critical_section(db, file.user_id):
        db.refresh(file)
        if not (file.meta or {}).get("skill") or file.deleted_at or file.purged_at or not file.is_head:
            raise HTTPException(409, "skill unavailable")
        row = db.get(SkillApproval, file_id, populate_existing=True)
        if row:
            row.state = body.state
        else:
            db.add(SkillApproval(file_id=file_id, state=body.state))
        record_audit(db, "skills.review", "file", file_id, actor_id=user.id, detail={"fields": [body.state]})
        db.commit()
    return {"file_id": file_id, "state": body.state}


def skill_input_for(db: Session, owner: User | None) -> dict:
    """Shared bootstrap fields for legacy GET and T14 model-input; ownership comes from the Run."""
    if owner is None:
        return {"disabled_skills": sorted(skills()), "personal_skills": []}
    return {
        "disabled_skills": run_disabled_skills(db, owner),
        "personal_skills": [
            {k: skill[k] for k in ("name", "title", "description", "file_id", "filename", "kind")}
            for skill in usable_personal(db, owner).values()
        ],
    }


class SkillContentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    content: str = Field(min_length=1, max_length=MAX_SKILL_MD)
    expected_file_id: str | None = Field(default=None, max_length=40)


@router.get("/content/{name}")
def read_skill_content(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..agent.prompts import load_skill
    from ..storage import get_storage

    if name in skills():
        return {
            "name": name,
            "content": load_skill(name),
            "file_id": None,
            "editable": False,
            "kind": platform_kind(name),
            "revision": None,
        }
    personal = next((s for s in personal_skills(db, user) if s["name"] == name), None)
    if not personal:
        raise HTTPException(404, "skill not found")
    file = db.get(FileObject, personal["file_id"])
    if file.status != FileStatus.READY or personal.get("error"):
        raise HTTPException(422, "技能尚未通过扫描，不能编辑")
    kind = personal.get("kind")
    content = None
    if kind == "md":
        if file.size > MAX_SKILL_MD:
            raise HTTPException(413, "skill text too large")
        content = get_storage().get_bytes(file.storage_key).decode("utf-8", "replace")
    return {"name": name, "content": content, "file_id": file.id, "editable": kind == "md", "kind": kind, "revision": file.id}


@router.post("/content", status_code=201)
def save_skill_content(body: SkillContentInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    import hashlib

    from ..library import assign_file_version, delete_storage_objects, trim_file_versions
    from ..models import new_id
    from ..quota import enforce_library_quota
    from ..skillfiles import frontmatter
    from ..storage import get_storage
    from .jobs import stage_job
    from .routes_internal import _skill_heads

    data = body.content.encode("utf-8")
    if len(data) > MAX_SKILL_MD:
        raise HTTPException(413, "skill text too large")
    meta = frontmatter(body.content)
    if meta.get("name") != body.name or not meta.get("description", "").strip():
        raise HTTPException(422, "SKILL.md 需要匹配的 name 和非空 description")
    if body.name in skills():
        raise HTTPException(422, "通用技能不能覆盖，请另取个人技能名称")
    storage = get_storage()
    with user_critical_section(db, user.id):
        heads = _skill_heads(db, user.id, body.name)
        parent = heads[0] if heads else None
        if (parent.id if parent else None) != body.expected_file_id:
            raise HTTPException(422, "技能已更新，请重新读取后再保存")
        if parent and (parent.status != FileStatus.READY or (parent.meta or {}).get("skill", {}).get("kind") != "md"):
            raise HTTPException(422, "只能编辑扫描完成的 Markdown 技能；压缩包请上传完整新版本")
        enforce_library_quota(db, user.id, len(data))
        fid = new_id("fil")
        key = f"skills/{user.id}/{fid}/SKILL.md"
        storage.put_bytes(key, data, "text/markdown")
        try:
            row = FileObject(
                id=fid,
                user_id=user.id,
                filename="SKILL.md",
                content_type="text/markdown",
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                storage_key=key,
                status=FileStatus.PENDING,
                purpose="skill",
                meta={"processing": "queued", "skill_claim": body.name, "source": "user"},
            )
            assign_file_version(db, row, parent)
            db.add(row)
            keys = trim_file_versions(db, row)
            db.flush()
            stage_job(db, "file", file_id=row.id)
            db.commit()
        except Exception:
            db.rollback()
            storage.delete(key)
            raise
    delete_storage_objects(keys)
    return {"file_id": fid, "name": body.name, "status": "pending"}
