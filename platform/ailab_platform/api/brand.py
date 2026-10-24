"""API-owned, immutable brand snapshots; pending replacements never hide ready assets."""

from contextlib import ExitStack
from copy import deepcopy

from sqlalchemy import select

from ..locks import user_critical_section
from ..models import Conversation, FileObject, FileStatus, PlatformSetting, Project, User, utcnow

KINDS = {"logo": "logo_file_id", "docx": "docx_template_file_id"}
MAX_TEMPLATE = 10 * 1024 * 1024


def brand_lock(db):
    # Same stable existing row used by platform context settings; serializes all administrators and Worker activation.
    return user_critical_section(db, db.scalar(select(User.id).order_by(User.id).limit(1)))


def brand_settings(db):
    row = db.get(PlatformSetting, "brand", populate_existing=True)
    defaults = {
        "enabled": False,
        "name": "",
        "colors": {"primary": "#1F4E79", "accent": "#E0A526"},
        "font": "",
        "version": 0,
        "updated_at": None,
        **{field: None for field in KINDS.values()},
    }
    return {**defaults, **deepcopy(row.value)} if row else defaults


def save_brand(db, value):
    value = deepcopy(value)
    value["version"] = value.get("version", 0) + 1
    value["updated_at"] = utcnow().isoformat()
    row = db.get(PlatformSetting, "brand")
    if row is None:
        db.add(PlatformSetting(key="brand", value=value))
    else:
        row.value = value
    db.flush()
    return value


def activate_asset(db, file):
    kind = (file.meta or {}).get("brand_kind")
    value = brand_settings(db)
    if kind not in KINDS or value.get("pending", {}).get(kind) != file.id:
        return
    if file.status == FileStatus.READY:
        if not (file.meta or {}).get("brand") or not (file.meta or {}).get("scan", {}).get("ok"):
            raise ValueError("brand assets require clean scan and successful analysis")
        # Frozen runs may still refer to the previous object. Retire only the settings pointer;
        # immutable file bytes must survive resume/replacement races.
        value[KINDS[kind]] = file.id
        value["pending"].pop(kind, None)
        save_brand(db, value)


def workspace_input(db, run):
    conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    project = db.get(Project, conversation.project_id) if conversation and conversation.project_id else None
    result = {"project_instructions": project.instructions if project else "", "brand": {}}
    if (run.options or {}).get("mode", "work") != "work":
        return result
    # The run's first bootstrap freezes the complete asset set, including disabled state.
    if "brand_version" in (run.runtime_meta or {}):
        result["brand"] = deepcopy(run.runtime_meta.get("brand") or {})
        return result
    with ExitStack() as locks:
        admin_id = db.scalar(select(User.id).order_by(User.id).limit(1))
        locks.enter_context(user_critical_section(db, admin_id))
        if admin_id != run.user_id:
            locks.enter_context(user_critical_section(db, run.user_id))
        db.refresh(run)
        meta = deepcopy(run.runtime_meta or {})
        if "brand_version" not in meta:
            value = brand_settings(db)
            brand = {}
            if value.get("enabled"):
                assets = {}
                for kind, field in KINDS.items():
                    file = db.get(FileObject, value.get(field)) if value.get(field) else None
                    if file and file.status == FileStatus.READY and file.purpose == "brand" and not file.purged_at:
                        assets[kind] = file.id
                brand = {k: value.get(k) for k in ("version", "name", "colors", "font")}
                brand["assets"] = assets
            meta.update(brand_version=value.get("version", 0), brand=brand)
            run.runtime_meta = meta
            db.commit()
        result["brand"] = deepcopy(meta.get("brand") or {})
    return result
