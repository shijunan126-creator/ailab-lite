"""Account memory: one bounded settings document, serialized with other account writes."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..contracts.memory import (
    MAX_CHARS,
    MAX_ENTRIES,
    MemoryDreamRunOut,
    MemoryDreamSettings,
    MemoryEntryInput,
    MemoryOperation,
    MemoryUpdate,
)
from ..deps import current_user, get_db
from ..locks import user_critical_section
from ..models import MemoryDreamRun, Run, RunDelivery, RunStatus, User, new_id
from ..service_auth import require_scope

router = APIRouter(tags=["memory"])


def _entry(entry: dict) -> dict:
    """Return a stored entry with Phase 2 fields filled for older records."""
    out = dict(entry)
    out.setdefault("confidence", 1.0)
    out.setdefault("evidence_run_ids", [])
    out.setdefault("valid_from", entry.get("updated_at"))
    out.setdefault("superseded_by", None)
    out.setdefault("last_used_at", None)
    return out


def memory_for(user: User, *, for_model: bool = False) -> dict:
    stored = (user.settings or {}).get("memory") or {}
    result = {
        "revision": 0,
        "enabled": True,
        "auto_save": True,
        "entries": [],
        **stored,
        "limit_chars": MAX_CHARS,
        "limit_entries": MAX_ENTRIES,
    }
    result["entries"] = [_entry(e) for e in result.get("entries") or []]
    if for_model:
        if not result["enabled"]:
            result["entries"] = []
        else:
            # A superseded fact stays visible to the user for audit, but the model must never
            # act on it again.
            result["entries"] = [e for e in result["entries"] if not e.get("superseded_by")]
    return result


def write_memory(db, user, body, source):
    previous = memory_for(user)
    if previous["revision"] != body.expected_revision:
        raise HTTPException(422, "记忆已更新，请重新读取后再保存")
    old = {e["id"]: e for e in previous["entries"]}
    now = datetime.now(UTC)
    entries = []
    for entry in body.entries:
        if entry.id and entry.id not in old:
            raise HTTPException(422, "unknown memory entry")
        prior = old.get(entry.id)
        if prior is None:
            entries.append(
                {
                    "id": entry.id or new_id("mem"),
                    "kind": entry.kind,
                    "content": entry.content,
                    "source": source,
                    "updated_at": now.isoformat(),
                    "confidence": entry.confidence if entry.confidence is not None else (1.0 if source == "user" else 0.8),
                    "evidence_run_ids": list(entry.evidence_run_ids or []),
                    "valid_from": (entry.valid_from or now).isoformat(),
                    "superseded_by": entry.superseded_by,
                    "last_used_at": entry.last_used_at.isoformat() if entry.last_used_at else None,
                }
            )
            continue
        merged = dict(prior)
        changed = prior["content"] != entry.content or prior["kind"] != entry.kind
        for field in ("confidence", "evidence_run_ids", "valid_from", "superseded_by", "last_used_at"):
            value = getattr(entry, field)
            if value is not None:
                merged[field] = value.isoformat() if field in {"valid_from", "last_used_at"} else value
        if merged["evidence_run_ids"]:
            merged["evidence_run_ids"] = list(dict.fromkeys(merged["evidence_run_ids"]))
        if changed:
            merged["content"] = entry.content
            merged["kind"] = entry.kind
            merged["updated_at"] = now.isoformat()
            merged["source"] = source
        entries.append(merged)
    value = {"revision": previous["revision"] + 1, "enabled": body.enabled, "auto_save": body.auto_save, "entries": entries}
    user.settings = {**(user.settings or {}), "memory": value}
    # Content never goes into audit logs or public events.
    record_audit(db, "memory.updated", "user", user.id, actor_id=user.id, detail={"revision": value["revision"], "source": source})
    return memory_for(user)


@router.get("/v1/memory")
def get_memory(user: User = Depends(current_user)):
    from ..memory_dream.settings import dream_settings

    return {**memory_for(user), "dream": dream_settings(user).model_dump(mode="json")}


@router.put("/v1/memory")
def update_memory(body: MemoryUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        db.refresh(user)
        result = write_memory(db, user, body, "user")
        db.commit()
    return result


@router.patch("/v1/memory/dream")
def update_dream_settings(body: MemoryDreamSettings, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..memory_dream.settings import save_dream_settings

    with user_critical_section(db, user.id):
        db.refresh(user)
        saved = save_dream_settings(db, user, body)
        record_audit(db, "memory.dream_settings", "user", user.id, actor_id=user.id, detail={"enabled": body.enabled})
        db.commit()
    return saved.model_dump(mode="json")


@router.post("/v1/memory/dream/run")
def run_dream_now(background: BackgroundTasks, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Manual trigger for QA and for a user who does not want to wait for the quiet window."""
    from ..memory_dream.service import process_dream
    from ..memory_dream.settings import dream_settings

    ds = dream_settings(user)
    if not ds.enabled:
        raise HTTPException(409, "nightly dream is disabled")
    zone_name = ds.timezone or "Asia/Shanghai"
    local_date = datetime.now(UTC).astimezone(ZoneInfo(zone_name)).date().isoformat()
    with user_critical_section(db, user.id):
        db.refresh(user)
        if db.scalar(select(Run.id).where(Run.user_id == user.id, Run.status.notin_(RunStatus.TERMINAL)).limit(1)):
            raise HTTPException(409, "wait for the current turn to finish")
        if db.scalar(
            select(MemoryDreamRun.id).where(
                MemoryDreamRun.user_id == user.id,
                MemoryDreamRun.local_date == local_date,
            )
        ):
            raise HTTPException(409, "dream already ran today")
        run = MemoryDreamRun(
            id=new_id("dream"),
            user_id=user.id,
            local_date=local_date,
            timezone=zone_name,
            status="pending",
            trigger="manual",
            input_run_ids=[],
            before_memory={},
            after_memory={},
            token_usage={},
        )
        db.add(run)
        db.commit()
    background.add_task(process_dream, run.id)
    return {"id": run.id}


@router.get("/v1/memory/dreams", response_model=list[MemoryDreamRunOut])
def list_dream_runs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..memory_dream.service import list_runs

    return list_runs(db, user.id)


@router.get("/v1/memory/dreams/metrics")
def dream_metrics(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..memory_dream.service import metrics

    return metrics(db, user.id)


@router.get("/v1/memory/dreams/{run_id}")
def get_dream_run(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..memory_dream.service import _run_out

    run = db.get(MemoryDreamRun, run_id)
    if not run or run.user_id != user.id:
        raise HTTPException(404, "dream run not found")
    out = _run_out(run).model_dump(mode="json")
    out["before"] = (run.before_memory or {}).get("entries") or []
    out["after"] = (run.after_memory or {}).get("entries") or []
    return out


@router.post("/v1/memory/dreams/{run_id}/revert")
def revert_dream_run(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..memory_dream.service import _run_out

    with user_critical_section(db, user.id):
        db.refresh(user)
        run = db.get(MemoryDreamRun, run_id)
        if not run or run.user_id != user.id:
            raise HTTPException(404, "dream run not found")
        if run.status != "applied":
            raise HTTPException(409, "only an applied dream can be reverted")
        before = run.before_memory or {}
        entries = []
        for raw in before.get("entries") or []:
            try:
                entries.append(
                    MemoryEntryInput(
                        id=raw["id"],
                        kind=raw["kind"],
                        content=raw["content"],
                        confidence=raw.get("confidence"),
                        evidence_run_ids=raw.get("evidence_run_ids"),
                        valid_from=raw.get("valid_from"),
                        superseded_by=raw.get("superseded_by"),
                        last_used_at=raw.get("last_used_at"),
                    )
                )
            except (KeyError, ValueError):
                raise HTTPException(409, "dream snapshot is not restorable")
        current = memory_for(user)
        result = write_memory(
            db,
            user,
            MemoryUpdate(
                expected_revision=current["revision"],
                enabled=before.get("enabled", current["enabled"]),
                auto_save=before.get("auto_save", current["auto_save"]),
                entries=entries,
            ),
            "user",
        )
        run.status = "reverted"
        run.after_memory = result
        db.commit()
    return _run_out(run).model_dump(mode="json")


@router.post("/internal/runs/{run_id}/memory", dependencies=[Depends(require_scope("memory:write_for_run"))])
def run_memory(
    run_id: str,
    body: MemoryOperation,
    x_run_attempt: str | None = Header(default=None),
    x_delivery_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    from .routes_internal import _run_or_404
    from .runtime_fence import fence_attempt

    run = _run_or_404(db, run_id)
    with user_critical_section(db, run.user_id):
        db.refresh(run)
        fence_attempt(db, run.id, x_run_attempt)
        if run.status != RunStatus.RUNNING:
            raise HTTPException(409, "run is not running")
        user = db.get(User, run.user_id, populate_existing=True)
        current = memory_for(user)
        if body.action == "list":
            return current if current["enabled"] else {**current, "entries": []}
        if not x_delivery_id or not x_delivery_id.startswith(run.id + ":") or len(x_delivery_id) > 160:
            raise HTTPException(422, "stable delivery identity required")
        prior = db.get(RunDelivery, (run.id, x_delivery_id))
        if prior:
            return prior.result
        if not current["enabled"] or (not current["auto_save"] and not body.explicit):
            raise HTTPException(422, "用户已关闭自动记忆；只在用户明确要求时操作记忆，关闭记忆时不能操作")
        entries = [MemoryEntryInput(**{k: e[k] for k in ("id", "kind", "content")}) for e in current["entries"]]
        if body.action == "add":
            item = MemoryEntryInput(kind=body.kind, content=body.content)
            if not any(e.kind == item.kind and e.content == item.content for e in entries):
                entries.append(item)
        else:
            match = next((i for i, e in enumerate(entries) if e.id == body.id), None)
            if match is None:
                raise HTTPException(422, "unknown memory entry; list memory again")
            if body.action == "remove":
                entries.pop(match)
            else:
                entries[match] = MemoryEntryInput(id=body.id, kind=body.kind, content=body.content)
        if body.expected_revision is None:
            raise HTTPException(422, "expected_revision is required; list memory again")
        try:
            update = MemoryUpdate(
                expected_revision=body.expected_revision, enabled=current["enabled"], auto_save=current["auto_save"], entries=entries
            )
        except ValueError as exc:
            raise HTTPException(422, "记忆内容为空或超出容量，请整理后重试") from exc
        result = write_memory(db, user, update, "agent")
        db.add(RunDelivery(run_id=run.id, delivery_id=x_delivery_id, result=result))
        db.commit()
        return result
