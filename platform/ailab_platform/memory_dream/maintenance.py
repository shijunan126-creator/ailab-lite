"""Fenced edit and project merge stages of a memory job."""

import asyncio
import copy
import json
import logging

from fastapi import HTTPException

from ..catalog import get_catalog
from ..config import settings
from ..contracts.memory import MemoryEntryInput, MemoryUpdate
from ..db import SessionLocal
from ..locks import user_critical_section
from ..memory_recall import guard
from ..models import Project, User, utcnow
from .changes import digest, record, scope_entries
from .prompts import EDIT_SYSTEM

log = logging.getLogger("ailab.memory")
LOST_CLAIM = "整理任务已由其他进程接手"


def inputs(entries):
    return [MemoryEntryInput.model_validate({k: v for k, v in e.items() if k in MemoryEntryInput.model_fields}) for e in entries]


def save(db, user, current, entries, *, explicit=False, tombstones=False):
    from ..api.routes_memory import write_memory

    return write_memory(
        db,
        user,
        MemoryUpdate(
            expected_revision=current["revision"], enabled=current["enabled"], auto_save=current["auto_save"], entries=inputs(entries)
        ),
        "user" if explicit else "nightly",
        explicit=explicit,
        tombstone_removed=tombstones,
    )


async def call(user, system, payload):
    from .service import _chat, _json

    raw, usage = await _chat(
        [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        settings.memory_dream_model or get_catalog().default_id(),
        user,
    )
    return _json(raw), usage


async def edit(run_id, token):
    from ..api.routes_memory import memory_for
    from .service import DreamError, _check_claim, _finish

    def prepare():
        with SessionLocal() as db:
            run = _check_claim(db, run_id, token)
            user = db.get(User, run.user_id)
            with user_critical_section(db, user.id):
                db.refresh(user)
                if user.disabled_at or not memory_for(user)["enabled"]:
                    raise DreamError("记忆未开启")
                if run.project_id:
                    project = db.get(Project, run.project_id, populate_existing=True)
                    if not project or project.memory_mode != "isolated":
                        raise DreamError("项目已删除或已改为共享记忆，不能单独修改")
                entries = scope_entries(user, run.project_id)
                if (run.report or {}).get("edit_source_digest", digest(entries)) != digest(entries):
                    raise DreamError("记忆已变化，请重试")
                return user, run.project_id, run.instruction, entries

    snapshot = await asyncio.to_thread(prepare)
    if snapshot is None:
        return
    user, pid, instruction, entries = snapshot
    result, usage = await call(
        user,
        EDIT_SYSTEM,
        {
            "instruction": instruction,
            "entries": [{k: e[k] for k in ("id", "kind", "content")} for e in entries],
        },
    )

    def apply():
        with SessionLocal() as db:
            with user_critical_section(db, user.id):
                run = _check_claim(db, run_id, token)
                owner = db.get(User, user.id, populate_existing=True)
                current = memory_for(owner)
                if scope_entries(owner, pid) != entries:
                    raise DreamError("记忆已变化，请重试")
                if owner.disabled_at or not current["enabled"] or (pid and not db.get(Project, pid)):
                    raise DreamError("记忆未开启或项目已删除")
                if pid and db.get(Project, pid, populate_existing=True).memory_mode != "isolated":
                    raise DreamError("项目已改为共享记忆，不能单独修改")
                allowed = {e["id"] for e in entries}
                modified = copy.deepcopy(current["entries"])
                refused = False
                counts = {"added": 0, "replaced": 0, "removed": 0}
                ops = result.get("ops") if isinstance(result.get("ops"), list) else []
                for op in ops[:100]:
                    action = op.get("op") if isinstance(op, dict) else None
                    if action not in {"add", "replace", "remove"}:
                        continue
                    if action != "remove":
                        # the model's text is checked like anything else that becomes memory; a malformed op is skipped, not fatal
                        try:
                            item = MemoryEntryInput(
                                kind=op.get("kind")
                                if action == "add"
                                and op.get("kind") in {"user", "memory", "fact", "preference", "project", "interest", "reference"}
                                else "memory",
                                content=str(op.get("content") or "").strip(),
                                project_id=pid,
                                origin="edited",
                            )
                        except ValueError:
                            continue
                        if guard.reject_reason(item.content, untrusted=False):
                            refused = True
                            continue
                    if action == "add":
                        if not any(e.get("project_id") == pid and e["content"] == item.content for e in modified):
                            from ..models import new_id

                            modified.append({**item.model_dump(mode="json"), "id": new_id("mem")})
                            counts["added"] += 1
                    elif op.get("id") in allowed:
                        if action == "remove":
                            if any(e["id"] == op["id"] for e in modified):
                                modified = [e for e in modified if e["id"] != op["id"]]
                                counts["removed"] += 1
                        else:
                            for e in modified:
                                if e["id"] == op["id"] and e["content"] != item.content:
                                    e.update(
                                        content=item.content, origin="edited", evidence_run_ids=[], evidence_episode_ids=[], source_quote=""
                                    )
                                    counts["replaced"] += 1
                changed = modified != current["entries"]
                try:
                    # Other scopes/settings may change while the model waits. Undo must restore the
                    # document just before this commit, rather than overwrite those concurrent changes.
                    after = save(db, owner, current, modified, explicit=True, tombstones=True) if changed else current
                except (ValueError, HTTPException) as exc:
                    raise DreamError("记忆已满，先忘记一些内容再试") from exc
                reply = str(result.get("reply") or ("已更新记忆。" if changed else "没有找到需要修改的记忆。"))[:500]
                if refused:
                    reply = "已处理可保存的内容；密钥或口令不能保存为记忆。" if changed else "密钥或口令不能保存为记忆。"
                # forgetting is final (the deletion lineage clears every copy), so only an edit without removals can be undone
                run.report = {**(run.report or {}), "changes": counts, "deletion": bool(counts["removed"])}
                run.report = {**(run.report or {}), "reply": reply, "_final_status": "applied" if changed else "skipped"}
                record(run, current, after)
                run.plan = {"edit": True}
                run.token_usage = usage
                _finish(db, run, "applied" if changed else "skipped")

    await asyncio.to_thread(apply)


def merge_project(db, user, project):
    from ..api.routes_memory import memory_for, write_memory
    from ..memory_recall.document import kind
    from ..memory_recall.tombstones import fact_hash
    from ..models import MemoryDreamRun, new_id

    before = memory_for(user)
    entries = copy.deepcopy(before["entries"])
    account = {(kind(e), fact_hash(e["content"])): e for e in entries if e.get("project_id") is None and e.get("status") == "active"}
    removed = set()
    for e in entries:
        if e.get("project_id") != project.id or e.get("status") != "active":
            continue
        key = (kind(e), fact_hash(e["content"]))
        if key in account:
            target = account[key]
            for field in ("evidence_run_ids", "evidence_episode_ids"):
                target[field] = list(dict.fromkeys([*target.get(field, []), *e.get(field, [])]))
                if len(target[field]) > 50:
                    from sqlalchemy import select

                    from ..models import MemoryEpisode, Run

                    table = Run if field == "evidence_run_ids" else MemoryEpisode
                    ordered = list(
                        db.scalars(
                            select(table.id)
                            .where(table.id.in_(target[field]))
                            .order_by((table.created_at if table is Run else table.observed_at).desc(), table.id.desc())
                            .limit(50)
                        )
                    )
                    target[field] = ordered
            if e.get("origin") in {"explicit", "manual", "edited"} and target.get("origin") == "inferred":
                target["origin"] = e["origin"]
            target["reinforced_at"] = utcnow().isoformat()
            removed.add(e["id"])
        else:
            e["project_id"] = None
            account[key] = e
    after = write_memory(
        db,
        user,
        MemoryUpdate(
            expected_revision=before["revision"],
            enabled=before["enabled"],
            auto_save=before["auto_save"],
            entries=inputs([e for e in entries if e["id"] not in removed]),
        ),
        "user",
        tombstone_removed=False,
        allow_overcap=True,
    )
    now = utcnow()
    job = MemoryDreamRun(
        id=new_id("dream"),
        user_id=user.id,
        project_id=project.id,
        trigger="merge",
        kind="merge",
        status="applied",
        local_date=now.date().isoformat(),
        timezone="UTC",
        created_at=now,
        finished_at=now,
        report={"merged_projects": [project.id], "chain_complete": True},
        plan={},
        token_usage={},
    )
    record(job, before, after)
    db.add(job)
    project.memory_merge_requested_at = None
    return job
