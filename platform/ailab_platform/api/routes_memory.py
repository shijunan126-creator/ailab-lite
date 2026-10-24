"""Account memory: one bounded settings document, serialized with other account writes."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..contracts.memory import MAX_CHARS, MAX_ENTRIES, MemoryEntryInput, MemoryOperation, MemoryUpdate
from ..deps import current_user, get_db
from ..locks import user_critical_section
from ..models import RunDelivery, RunStatus, User, new_id
from ..service_auth import require_scope

router = APIRouter(tags=["memory"])


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
    if for_model and not result["enabled"]:
        result["entries"] = []
    return result


def write_memory(db, user, body, source):
    previous = memory_for(user)
    if previous["revision"] != body.expected_revision:
        raise HTTPException(422, "记忆已更新，请重新读取后再保存")
    old = {e["id"]: e for e in previous["entries"]}
    entries = []
    for entry in body.entries:
        if entry.id and entry.id not in old:
            raise HTTPException(422, "unknown memory entry")
        prior = old.get(entry.id)
        unchanged = prior and prior["content"] == entry.content and prior["kind"] == entry.kind
        entries.append(
            prior
            if unchanged
            else {
                "id": entry.id or new_id("mem"),
                "kind": entry.kind,
                "content": entry.content,
                "source": source,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
    value = {"revision": previous["revision"] + 1, "enabled": body.enabled, "auto_save": body.auto_save, "entries": entries}
    user.settings = {**(user.settings or {}), "memory": value}
    # Content never goes into audit logs or public events.
    record_audit(db, "memory.updated", "user", user.id, actor_id=user.id, detail={"revision": value["revision"], "source": source})
    return memory_for(user)


@router.get("/v1/memory")
def get_memory(user: User = Depends(current_user)):
    return memory_for(user)


@router.put("/v1/memory")
def update_memory(body: MemoryUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        db.refresh(user)
        result = write_memory(db, user, body, "user")
        db.commit()
    return result


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
