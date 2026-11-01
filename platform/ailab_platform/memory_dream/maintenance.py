"""Fenced edit and project merge stages of a memory job."""

import asyncio
import copy
import json
import logging

from sqlalchemy import select

from ..catalog import get_catalog
from ..config import settings
from ..contracts.memory import MemoryEntryInput, MemoryUpdate
from ..db import SessionLocal
from ..locks import user_critical_section
from ..memory_recall import guard
from ..memory_recall.policy import visible
from ..models import Project, User, utcnow
from .prompts import EDIT_SYSTEM, MERGE_SYSTEM
from .summary import _facts, regenerate, scope_entries, stale_scopes, summary_row

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
            if run.after_memory and (run.report or {}).get("_final_status") in {"applied", "skipped"}:
                return
            user = db.get(User, run.user_id)
            with user_critical_section(db, user.id):
                db.refresh(user)
                if user.disabled_at or not memory_for(user)["enabled"]:
                    raise DreamError("记忆未开启")
                entries = scope_entries(user, run.project_id)
                before = memory_for(user)
                if run.before_memory and _facts(run.before_memory, run.project_id) != entries:
                    raise DreamError("记忆已变化，请重试")
                row = summary_row(db, user.id, run.project_id)
                snapshot = {
                    "text": row.text if row else "",
                    "source_digest": row.source_digest if row else "",
                    "generated_at": row.generated_at.isoformat() if row and row.generated_at else None,
                }
                run.before_memory = before
                run.report = run.report or {"summary_before": snapshot}
                db.commit()
                return user, run.project_id, run.instruction, entries

    snapshot = await asyncio.to_thread(prepare)
    if snapshot is None:
        return
    user, pid, instruction, entries = snapshot
    result, usage = await call(
        user, EDIT_SYSTEM, {"instruction": instruction, "entries": [{k: e[k] for k in ("id", "kind", "content")} for e in entries]}
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
                                kind=op.get("kind") if action == "add" and op.get("kind") in {"user", "memory"} else "memory",
                                content=str(op.get("content") or "").strip(),
                                project_id=pid,
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
                                    e.update(content=item.content, evidence_run_ids=[], evidence_episode_ids=[], source_quote="")
                                    counts["replaced"] += 1
                changed = modified != current["entries"]
                try:
                    after = save(db, owner, current, modified, explicit=True, tombstones=True) if changed else current
                except ValueError:
                    raise DreamError("记忆已满，先忘记一些内容再试")
                reply = str(result.get("reply") or ("已更新记忆。" if changed else "没有找到需要修改的记忆。"))[:500]
                if refused:
                    reply = "已处理可保存的内容；密钥或口令不能保存为记忆。" if changed else "密钥或口令不能保存为记忆。"
                # forgetting is final (the deletion lineage clears every copy), so only an edit without removals can be undone
                run.report = {**(run.report or {}), "changes": counts}
                run.report = {**(run.report or {}), "reply": reply, "_final_status": "applied" if changed else "skipped"}
                run.after_memory = after
                run.plan = {"edit": True}
                run.token_usage = usage
                _finish(db, run, "applied" if changed else "skipped")

    await asyncio.to_thread(apply)


async def merge_projects(run_id, token):
    from ..api.routes_memory import memory_for
    from .service import DreamError, _check_claim

    def pending():
        with SessionLocal() as db:
            run = _check_claim(db, run_id, token)
            return list(
                db.scalars(
                    select(Project.id)
                    .where(Project.user_id == run.user_id, Project.memory_merge_requested_at.is_not(None))
                    .order_by(Project.memory_merge_requested_at, Project.id)
                    .limit(5)
                )
            )

    async def merge_one(pid):

        def prepare():
            with SessionLocal() as db:
                run = _check_claim(db, run_id, token)
                with user_critical_section(db, run.user_id):
                    project = db.get(Project, pid, populate_existing=True)
                    if not project or not project.memory_merge_requested_at:
                        return
                    if project.memory_mode != "shared":
                        project.memory_merge_requested_at = None
                        db.commit()
                        return
                    user = db.get(User, run.user_id, populate_existing=True)
                    current = memory_for(user)
                    if user.disabled_at or not current["enabled"]:
                        raise DreamError("记忆未开启")
                    active = [e for e in current["entries"] if visible(e, e.get("project_id"))]
                    return (
                        user,
                        current,
                        [e for e in active if e.get("project_id") == pid],
                        [e for e in active if e.get("project_id") is None],
                        project.memory_merge_requested_at,
                    )

        snapshot = await asyncio.to_thread(prepare)
        if not snapshot:
            return
        user, before, entries, account, requested = snapshot
        plan, _ = (
            await call(
                user,
                MERGE_SYSTEM,
                {
                    "project": [{k: e.get(k) for k in ("id", "kind", "content", "valid_from")} for e in entries],
                    "account": [{k: e.get(k) for k in ("id", "kind", "content", "valid_from")} for e in account],
                },
            )
            if entries
            else ({"decisions": []}, {})
        )

        def apply():
            with SessionLocal() as db:
                with user_critical_section(db, user.id):
                    run = _check_claim(db, run_id, token)
                    project = db.get(Project, pid, populate_existing=True)
                    if not project:
                        return
                    if project.memory_mode != "shared":
                        project.memory_merge_requested_at = None
                        db.commit()
                        return
                    owner = db.get(User, user.id, populate_existing=True)
                    current = memory_for(owner)
                    if (
                        owner.disabled_at
                        or not current["enabled"]
                        or current["revision"] != before["revision"]
                        or project.memory_merge_requested_at != requested
                    ):
                        raise DreamError("记忆已变化，请重试")
                    decisions = plan.get("decisions", [])
                    if len(decisions) != len(entries) or {d.get("id") for d in decisions} != {e["id"] for e in entries}:
                        raise DreamError("项目融合计划不完整")
                    modified = copy.deepcopy(current["entries"])
                    by_id = {e["id"]: e for e in modified}
                    account_ids = {e["id"] for e in account}
                    removed = set()
                    for decision in decisions:
                        entry = by_id[decision["id"]]
                        action = decision.get("action")
                        if action == "to_account":
                            entry["project_id"] = None
                        elif action in {"duplicate_of", "contradicts"}:
                            if decision.get("account_id") not in account_ids:
                                raise DreamError("项目融合目标无效")
                            target = by_id[decision["account_id"]]
                            if action == "duplicate_of":
                                for key in ("evidence_run_ids", "evidence_episode_ids"):
                                    target[key] = list(dict.fromkeys([*target.get(key, []), *entry.get(key, [])]))
                                removed.add(entry["id"])
                            else:
                                from datetime import UTC, datetime

                                from .service import _aware

                                def at(e):
                                    return (
                                        _aware(datetime.fromisoformat(e["valid_from"]))
                                        if e.get("valid_from")
                                        else datetime.min.replace(tzinfo=UTC)
                                    )

                                newer, older = (entry, target) if at(entry) > at(target) else (target, entry)
                                older.update(invalid_at=newer.get("valid_from") or utcnow().isoformat(), superseded_by=newer["id"])
                                if newer is entry:
                                    entry["project_id"] = None
                        elif action != "keep_project":
                            raise DreamError("项目融合操作无效")
                    run.before_memory = run.before_memory or before
                    after = (
                        save(db, owner, current, [e for e in modified if e["id"] not in removed])
                        if modified != current["entries"] or removed
                        else current
                    )
                    run.after_memory = after
                    run.plan = {**(run.plan or {}), "merges": [*(run.plan or {}).get("merges", []), {"project_id": pid, **plan}]}
                    run.report = {**(run.report or {}), "merged_projects": [*(run.report or {}).get("merged_projects", []), pid]}
                    project.memory_merge_requested_at = None
                    db.commit()

        await asyncio.to_thread(apply)

    for pid in await asyncio.to_thread(pending):
        try:
            await merge_one(pid)
        except Exception as exc:
            if isinstance(exc, DreamError) and str(exc) == LOST_CLAIM:
                raise
            # One project's bad plan or a model failure must not stop the others, the extraction or the summaries:
            # its data and its merge request stay as they were, and the next consolidation tries again.
            log.warning("memory project merge failed run=%s error=%s", run_id, type(exc).__name__)

            def note(message=str(exc)[:200] if isinstance(exc, DreamError) else "融合未完成"):
                with SessionLocal() as db:
                    run = _check_claim(db, run_id, token)
                    run.report = {**(run.report or {}), "merge_errors": [*(run.report or {}).get("merge_errors", []), message]}
                    db.commit()

            await asyncio.to_thread(note)


async def finish_summaries(run_id, token):
    from ..models import MemoryDreamRun

    def prepare():
        with SessionLocal() as db:
            run = db.get(MemoryDreamRun, run_id)
            if not run or run.claim_token != token or run.status not in {"applied", "skipped", "failed"}:
                return
            user = db.get(User, run.user_id)
            if not user or user.disabled_at:
                return
            return user.id, stale_scopes(db, user, limit=5, requested=run.project_id)

    prepared = await asyncio.to_thread(prepare)
    if not prepared:
        return
    uid, scopes = prepared
    for pid in scopes:
        await regenerate(uid, pid)
