"""Nightly memory consolidation.

The pipeline deliberately follows the separation used by production memory systems:

1. ingest immutable episodes;
2. extract candidate facts (generous, Mem0-style ADD-only prompt);
3. deterministic dedupe (hash + fuzzy candidate selection);
4. ask a separate conservative judge about duplicates/contradictions (Graphiti-style prompt);
5. append new facts and invalidate old facts instead of overwriting them;
6. snapshot and audit the whole night.

No Run, RunEvent or sandbox is created. A dream is a platform job, not a conversation turn.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from rapidfuzz import fuzz
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..catalog import get_catalog
from ..config import settings
from ..contracts.memory import (
    DREAM_MAX_CHANGES,
    MAX_CHARS,
    DreamChange,
    DreamPlan,
    MemoryDreamRunOut,
    MemoryEntryInput,
    MemoryUpdate,
)
from ..db import SessionLocal
from ..llm import llm_for
from ..locks import user_critical_section
from ..memory_recall import guard
from ..memory_recall.policy import run_permissions, run_project, visible
from ..memory_recall.tombstones import blocked_by_tombstone, tombstones_of
from ..metrics import MEMORY_GUARD_REJECTED
from ..models import Conversation, MemoryDreamRun, MemoryEpisode, MemoryWorkItem, PlatformSetting, Run, RunStatus, User, new_id, utcnow
from .prompts import EXTRACT_SYSTEM, JUDGE_SYSTEM
from .settings import dream_settings, local_window

log = logging.getLogger("ailab.memory_dream")

TERMINAL = set(RunStatus.TERMINAL)
dream_call: ContextVar[tuple[str, str] | None] = ContextVar("dream_call", default=None)


class DreamError(RuntimeError):
    pass


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return utcnow()
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _normalize(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.casefold()).strip()


def _hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def _memory_view(user: User) -> dict:
    from ..api.routes_memory import memory_for

    current = memory_for(user)
    return {
        "revision": current["revision"],
        "enabled": current["enabled"],
        "auto_save": current["auto_save"],
        "entries": current["entries"],
    }


def _active(entries: list[dict]) -> list[dict]:
    return [e for e in entries if visible(e, e.get("project_id"))]


def _run_out(run: MemoryDreamRun) -> MemoryDreamRunOut:
    plan = run.plan or {}
    changes = []
    from sqlalchemy.orm import object_session

    from ..api.source_lifecycle import visible_memory_entries

    db = object_session(run)
    items = plan.get("changes") or []
    if db:
        items = visible_memory_entries(db, run.user_id, items)
    for item in items:
        try:
            changes.append(DreamChange.model_validate(item))
        except ValueError:
            continue
    return MemoryDreamRunOut(
        id=run.id,
        status=run.status,
        trigger=run.trigger,
        local_date=run.local_date,
        timezone=run.timezone,
        cursor_run_id=run.cursor_run_id,
        input_run_ids=run.input_run_ids or [],
        changes=changes,
        journal=run.journal or "",
        token_usage=run.token_usage or {},
        error=run.error or "",
        created_at=_aware(run.created_at) if run.created_at else None,
        finished_at=_aware(run.finished_at) if run.finished_at else None,
    )


def _last_success(db: Session, user_id: str) -> MemoryDreamRun | None:
    return db.scalar(
        select(MemoryDreamRun)
        .where(MemoryDreamRun.user_id == user_id, MemoryDreamRun.status.in_(("applied", "reverted")))
        .order_by(MemoryDreamRun.finished_at.desc(), MemoryDreamRun.created_at.desc())
        .limit(1)
    )


def _new_runs(db: Session, user_id: str, last: MemoryDreamRun | None, now: datetime) -> list[Run]:
    from ..api.source_lifecycle import invalid_sources_query

    processed = (
        select(MemoryWorkItem.run_id)
        .where(MemoryWorkItem.run_id == Run.id, MemoryWorkItem.user_id == user_id, MemoryWorkItem.state.in_(("done", "skipped")))
        .exists()
    )
    return list(
        db.scalars(
            select(Run)
            .outerjoin(Conversation, Conversation.id == Run.conversation_id)
            .where(
                Run.user_id == user_id,
                Run.id.not_in(invalid_sources_query(user_id)),
                Run.status.in_(TERMINAL),
                ~processed,
                Conversation.memory_policy["write"].as_boolean().is_not(False),
                Run.options["memory_policy"]["write"].as_boolean().is_not(False),
                Run.superseded_by_run_id.is_(None),  # the user rewrote this turn; what they replaced is not what they meant
                or_(Run.conversation_id.is_(None), Conversation.deleted_at.is_(None)),  # a deleted conversation is not evidence
            )
            .order_by(Run.created_at, Run.id)
            .limit(40)
        )
    )


def _candidate_users(db: Session, now: datetime) -> list[User]:
    cursor = db.get(PlatformSetting, "memory_dream_cursor")
    after = (cursor.value or {}).get("after", "") if cursor else ""
    users = list(db.scalars(select(User).where(User.disabled_at.is_(None), User.id > after).order_by(User.id).limit(200)))
    value = {"after": users[-1].id if len(users) == 200 else ""}
    if cursor:
        cursor.value = value
    else:
        db.add(PlatformSetting(key="memory_dream_cursor", value=value))
    db.commit()
    return users


def reclaim_failed(db: Session, row: MemoryDreamRun, *, trigger: str) -> MemoryDreamRun:
    """A failed night is retryable the same day: reuse the row (the date is unique) and start over."""
    row.status = "pending"
    row.trigger = trigger
    row.error = ""
    row.journal = ""
    row.plan = {}
    row.claim_token = None
    row.claimed_until = None
    row.started_at = None
    row.finished_at = None
    db.add(row)
    return row


def claim_due(now: datetime | None = None) -> list[str]:
    now = _aware(now)
    claimed: list[str] = []
    with SessionLocal() as db:
        for user in _candidate_users(db, now):
            if len(claimed) >= settings.memory_dream_max_users:
                cursor = db.get(PlatformSetting, "memory_dream_cursor")
                cursor.value = {"after": user.id}
                db.commit()
                break
            ds = dream_settings(user)
            document = _memory_view(user)
            if not ds.enabled or not document["enabled"] or not document["auto_save"]:
                continue
            zone_name = ds.timezone or settings.memory_dream_default_timezone
            try:
                zone = ZoneInfo(zone_name)
            except Exception:
                zone = ZoneInfo(settings.memory_dream_default_timezone)
            local = now.astimezone(zone)
            start, end = local_window(ds)
            if not start <= local.hour * 60 + local.minute < end:
                continue
            if db.scalar(select(Run.id).where(Run.user_id == user.id, Run.status.notin_(TERMINAL)).limit(1)):
                continue
            runs = _new_runs(db, user.id, _last_success(db, user.id), now)
            if not runs:
                continue
            local_date = local.date().isoformat()
            existing = db.scalar(
                select(MemoryDreamRun).where(
                    MemoryDreamRun.user_id == user.id,
                    MemoryDreamRun.local_date == local_date,
                )
            )
            if existing is not None:
                if existing.status != "failed":
                    continue
                # Last night's attempt failed: retry it in this window instead of losing the day.
                row = reclaim_failed(db, existing, trigger="cron")
                row.input_run_ids = [r.id for r in runs]
            else:
                row = MemoryDreamRun(
                    id=new_id("dream"),
                    user_id=user.id,
                    local_date=local_date,
                    timezone=zone_name,
                    status="pending",
                    trigger="cron",
                    input_run_ids=[r.id for r in runs],
                    before_memory={},
                    after_memory={},
                    token_usage={},
                )
                db.add(row)
            try:
                db.commit()
                claimed.append(row.id)
            except IntegrityError:
                db.rollback()
        return claimed


ASSISTANT_MARK = "\n\n助手："


def _digest(run: Run, user_limit: int = 4000, assistant_limit: int = 2000) -> str:
    # cut each side on its own, so a long prompt cannot push the marker out and turn assistant text into "user_said"
    return f"用户：{(run.prompt or '').strip()[:user_limit]}{ASSISTANT_MARK}{(run.summary or '').strip()[:assistant_limit]}"


def _user_part(digest: str) -> str:
    """What the user wrote. Split on the *first* marker, so text the user pasted can only shrink this."""
    if digest.startswith('{"user_said":'):
        return json.loads(digest)["user_said"]
    head = digest.split(ASSISTANT_MARK, 1)[0]
    return head.removeprefix("用户：")


def _source_text(db: Session, run: Run) -> str:
    from ..models import QueuedMessage

    ids = (run.runtime_meta or {}).get("steer_ids") or []
    steers = (
        dict(
            db.execute(
                select(QueuedMessage.id, QueuedMessage.prompt).where(
                    QueuedMessage.run_id == run.id, QueuedMessage.state == "steered", QueuedMessage.id.in_(ids)
                )
            ).all()
        )
        if ids
        else {}
    )
    return "\n\n".join([run.prompt or "", *(steers[i] for i in ids if i in steers)])


def _episodes(db: Session, user: User, runs: list[Run]) -> list[MemoryEpisode]:
    """Create immutable episode rows once; reuse them on a retry."""
    existing = {
        e.content_hash: e
        for e in db.scalars(select(MemoryEpisode).where(MemoryEpisode.user_id == user.id, MemoryEpisode.run_id.in_([r.id for r in runs])))
    }
    out: list[MemoryEpisode] = []
    for run in runs:
        item = db.get(MemoryWorkItem, run.id)
        offset = item.source_offset if item else 0
        source = _source_text(db, run)
        chunk_chars = max(100, min(4000, (settings.memory_dream_input_chars - 500) // 3))
        digest = json.dumps(
            {
                "user_said": source[offset : offset + chunk_chars],
                "assistant_replied": (run.summary or "")[: min(1500, chunk_chars)],
                "source_end": min(offset + chunk_chars, len(source)),
            },
            ensure_ascii=False,
        )
        content_hash = _hash(run.id + "\n" + str(offset) + "\n" + digest)
        episode = existing.get(content_hash)
        if episode is None:
            episode = MemoryEpisode(
                id=new_id("epi"),
                user_id=user.id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                observed_at=_aware(run.created_at),
                content_hash=content_hash,
                digest=digest,
                token_count=max(1, len(digest) // 4),
            )
            db.add(episode)
            existing[content_hash] = episode
        if episode not in out:
            out.append(episode)
    db.flush()
    return out


def _episode_payload(episodes: list[MemoryEpisode]) -> str:
    return json.dumps(
        [
            {
                "id": e.id,
                "run_id": e.run_id,
                "observed_at": _aware(e.observed_at).isoformat(),
                "user_said": _user_part(e.digest),
                "assistant_replied": (
                    json.loads(e.digest).get("assistant_replied", "")
                    if e.digest.startswith('{"user_said":')
                    else e.digest.split(ASSISTANT_MARK, 1)[1]
                    if ASSISTANT_MARK in e.digest
                    else ""
                ),
            }
            for e in episodes
        ],
        ensure_ascii=False,
    )


def _fit_episodes(episodes: list[MemoryEpisode], budget: int) -> list[MemoryEpisode]:
    """Fit a complete oldest prefix; unprocessed episodes remain eligible for the next batch."""
    kept, payload = episodes, _episode_payload(episodes)
    while len(payload) > budget and len(kept) > 1:
        kept = kept[:-1]
        payload = _episode_payload(kept)
    return kept


def _quote_supported(quote: str, episodes: list[MemoryEpisode]) -> bool:
    """A quote counts only if the *user* said it: assistant text is context, never evidence (doc §7.2)."""
    q = _normalize(quote)
    return bool(q) and any(q in _normalize(_user_part(e.digest)) for e in episodes)


async def _chat(messages: list[dict], model: str, user: User) -> tuple[str, dict]:
    from uuid import uuid4

    from ..contracts.usage import ReserveUsageIn, SettleUsageIn
    from ..llm import ModelCall, model_call
    from ..model_policy import estimator
    from ..quota import reserve_usage, settle_usage

    timeout = min(240, settings.llm_total_timeout_seconds or settings.llm_timeout_seconds)
    identity = dream_call.get()
    call = ModelCall(uuid4().hex, "", settings.memory_dream_max_tokens, actual_backend=model, quiet=True)
    estimate = estimator.estimate(model, {"messages": messages, "tools": []})
    from ..model_policy import ModelPolicy

    spec = get_catalog().get(model)
    if spec is not None and estimate > ModelPolicy(get_catalog()).input_budget(spec):
        raise DreamError("记忆整理输入超出模型上下文预算")
    if identity:
        with SessionLocal() as db:
            reserve_usage(
                db,
                ReserveUsageIn(
                    call_id=call.call_id,
                    subject="memory_job",
                    job_id=identity[0],
                    attempt_id=identity[1],
                    alias=model,
                    mode="memory",
                    quiet=True,
                    max_output_tokens=call.max_output_tokens,
                    estimated_input_tokens=estimate,
                ),
            )
    elif settings.env != "test":
        raise DreamError("模型抽取必须通过有计费身份的后台任务运行")
    token = model_call.set(call)
    response = None
    try:
        async with asyncio.timeout(timeout):
            response = await llm_for(model).chat(messages, [], model, user_id=user.id, max_tokens=call.max_output_tokens)
        call.reached_model = True
        call.usage = response.usage or {}
        call.output_bytes = len((response.content + response.reasoning_content).encode())
    finally:
        model_call.reset(token)
        if identity:
            usage = call.usage
            status = "settled" if usage else "estimated" if call.reached_model else "released"
            with SessionLocal() as db:
                settle_usage(
                    db,
                    SettleUsageIn(
                        call_id=call.call_id,
                        attempt_id=identity[1],
                        actual_backend=call.actual_backend,
                        input_tokens=usage.get("prompt_tokens", estimate if call.reached_model else 0),
                        output_tokens=usage.get("completion_tokens", (call.output_bytes + 3) // 4 if call.reached_model else 0),
                        cached_tokens=usage.get("cached_tokens", 0),
                        reasoning_tokens=usage.get("reasoning_tokens", 0),
                        status=status,
                    ),
                )
    if response.tool_calls or not response.content.strip():
        raise DreamError("dream model returned no final JSON")
    return response.content, dict(response.usage or {})


def _json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise DreamError("dream model did not return a JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise DreamError(f"invalid JSON: {exc}") from exc


async def _extract(episodes: list[MemoryEpisode], active: list[dict], user: User) -> tuple[list[dict], dict, str]:
    model = settings.memory_dream_model or get_catalog().default_id()
    if not get_catalog().get(model):
        raise DreamError(f"dream model not found: {model}")
    existing = [{"id": e["id"], "text": e["content"], "valid_from": e.get("valid_from")} for e in active]
    payload = _episode_payload(_fit_episodes(episodes, settings.memory_dream_input_chars))
    user_prompt = (
        "已有事实（用于避免重复；不要从这些事实里再抽取）：\n"
        + json.dumps(existing, ensure_ascii=False)
        + "\n\n新 episodes：\n"
        + payload
        + "\n\n只返回符合要求的 JSON。"
    )
    raw, usage = await _chat(
        [{"role": "system", "content": EXTRACT_SYSTEM}, {"role": "user", "content": user_prompt}],
        model,
        user,
    )
    payload = _json(raw)
    facts = payload.get("facts")
    if not isinstance(facts, list):
        raise DreamError("extract response has no facts list")
    episodes = _fit_episodes(episodes, settings.memory_dream_input_chars)
    out: list[dict] = []
    for item in facts[:100]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        quote = str(item.get("source_quote") or "").strip()
        supported = [e for e in episodes if _quote_supported(quote, [e])]
        if not text or not supported or len(text) > 1000 or len(quote) > 1000:
            continue
        evidence = {e.run_id for e in supported if e.run_id}
        episode_evidence = {e.id for e in supported}
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        if not 0 <= confidence <= 1 or confidence < settings.memory_dream_confidence:
            continue
        entities = [str(x).strip()[:120] for x in item.get("entities") or [] if str(x).strip()][:20]
        out.append(
            {
                "text": text,
                "kind": "user" if item.get("kind") == "user" else "memory",
                "entities": entities,
                "evidence_run_ids": sorted(evidence),
                "evidence_episode_ids": sorted(episode_evidence),
                "source_quote": quote,
                "confidence": confidence,
            }
        )
    return out, usage, model


def _screen(facts: list[dict], tombstones: list[dict]) -> list[dict]:
    """Drop what must not be stored however well it was quoted: credentials, instructions, deleted facts."""
    kept = []
    for fact in facts:
        reason = guard.reject_reason(fact["text"])
        if reason:
            MEMORY_GUARD_REJECTED.labels(reason, "nightly").inc()
        elif not blocked_by_tombstone(tombstones, fact["text"], fact.get("evidence_run_ids")):
            kept.append(fact)
    return kept


def _candidates(fact: dict, active: list[dict]) -> list[dict]:
    text = _normalize(fact["text"])
    scored: list[tuple[float, dict]] = []
    for old in active:
        score = fuzz.token_set_ratio(text, _normalize(old["content"]))
        entity_overlap = any(x.casefold() == y.casefold() for x in fact.get("entities") or [] for y in old.get("entities") or [])
        if score >= 55 or entity_overlap:
            scored.append((score, old))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [old for _, old in scored[:8]]


async def _judge(facts: list[dict], active: list[dict], user: User, model: str) -> tuple[dict[int, dict], dict]:
    """Return per-fact {"duplicate_ids": [...], "contradicted_ids": [...]} using a strict judge."""
    work = []
    for index, fact in enumerate(facts):
        if len(work) >= DREAM_MAX_CHANGES:
            break
        candidates = _candidates(fact, active)
        work.append(
            {
                "index": index,
                "new": {"text": fact["text"], "source_quote": fact["source_quote"]},
                "existing": [{"id": c["id"], "text": c["content"], "valid_from": c.get("valid_from")} for c in candidates],
            }
        )
        while len(json.dumps(work[-1], ensure_ascii=False)) > settings.memory_dream_input_chars and work[-1]["existing"]:
            work[-1]["existing"].pop()
    if not work:
        return {}, {}
    payload = json.dumps(work, ensure_ascii=False)
    raw, usage = await _chat(
        [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": payload + "\n\n只返回符合要求的 JSON。"},
        ],
        model,
        user,
    )
    data = _json(raw)
    results = data.get("results")
    if not isinstance(results, list):
        raise DreamError("judge response has no results list")
    by_index = {int(item["index"]): item for item in work if "index" in item}
    out: dict[int, dict] = {index: {"deferred": True} for index in range(len(facts)) if index not in by_index}
    out.update({index: {"supported": False, "duplicate_ids": [], "contradicted_ids": []} for index in by_index})
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if index not in by_index:
            continue
        valid_ids = {c["id"] for c in by_index[index]["existing"]}
        out[index] = {
            "supported": item.get("supported") is True,
            "duplicate_ids": [x for x in item.get("duplicate_ids") or [] if x in valid_ids],
            "contradicted_ids": [x for x in item.get("contradicted_ids") or [] if x in valid_ids],
        }
    return out, usage


def _fit(entries: list[MemoryEntryInput]) -> list[MemoryEntryInput]:
    """Keep active facts and recent invalid history inside the JSON budget."""
    total = sum(len(e.content) for e in entries)
    if total <= MAX_CHARS and len(entries) <= 100:
        return entries
    raise DreamError("记忆容量已满，请删除不再需要的内容后继续整理")


def _apply(
    db: Session, user: User, plan: DreamPlan, episode_by_run: dict[str, MemoryEpisode], *, expected_revision: int | None = None
) -> dict:
    from ..api.routes_memory import memory_for, write_memory

    with user_critical_section(db, user.id):
        db.refresh(user)
        current = memory_for(user)
        if not current["enabled"] or not current["auto_save"] or not dream_settings(user).enabled or user.disabled_at:
            raise DreamError("自动记忆已关闭")
        if expected_revision is not None and current["revision"] != expected_revision:
            raise DreamError("记忆已更新，稍后基于最新内容重试")
        entries = [
            MemoryEntryInput(
                id=e["id"],
                kind=e["kind"],
                content=e["content"],
                confidence=e.get("confidence"),
                evidence_run_ids=e.get("evidence_run_ids"),
                evidence_episode_ids=e.get("evidence_episode_ids"),
                entities=e.get("entities"),
                source_quote=e.get("source_quote"),
                valid_from=e.get("valid_from"),
                invalid_at=e.get("invalid_at"),
                superseded_by=e.get("superseded_by"),
                last_used_at=e.get("last_used_at"),
                suppressed=e.get("suppressed"),
                project_id=e.get("project_id"),
                status=e.get("status"),
            )
            for e in current["entries"]
        ]
        by_id = {e.id: e for e in entries}
        added = 0
        now = utcnow()
        for change in plan.changes:
            if change.op == "add":
                if blocked_by_tombstone(tombstones_of(user), change.content, change.evidence_run_ids):
                    raise DreamError("候选记忆已被用户删除")
                for rid in change.evidence_run_ids:
                    source = db.get(Run, rid, populate_existing=True)
                    conv = (
                        db.get(Conversation, source.conversation_id, populate_existing=True) if source and source.conversation_id else None
                    )
                    if not source or source.user_id != user.id or source.superseded_by_run_id or (conv and conv.deleted_at):
                        raise DreamError("候选记忆来源已不可用")
                    if not run_permissions(db, source)["write"]:
                        raise DreamError("来源对话关闭了记忆写入")
                    if run_project(db, source) != change.project_id:
                        raise DreamError("候选记忆来源范围已改变")
                episode = next(
                    (episode_by_run[rid] for rid in change.evidence_run_ids if rid in episode_by_run),
                    None,
                )
                entries.append(
                    MemoryEntryInput(
                        id=new_id("mem"),
                        kind=change.kind,
                        content=change.content,
                        confidence=change.confidence,
                        evidence_run_ids=change.evidence_run_ids,
                        evidence_episode_ids=change.evidence_episode_ids,
                        entities=change.entities,
                        source_quote=change.source_quote,
                        valid_from=(episode.observed_at if episode else now),
                        project_id=change.project_id,
                    )
                )
                for old_id in change.supersedes:
                    if old_id in by_id:
                        target = by_id[old_id]
                        observed = _aware(entries[-1].valid_from)
                        if target.valid_from and observed < _aware(target.valid_from):
                            # A late backfill must not replace a newer confirmed state.
                            entries[-1].invalid_at = target.valid_from
                            entries[-1].superseded_by = target.id
                        else:
                            target.invalid_at = observed
                            target.superseded_by = entries[-1].id
                added += 1
            elif change.op == "invalidate" and change.id in by_id:
                target = by_id[change.id]
                target.invalid_at = now
            else:
                continue
        if not added and not any(c.op == "invalidate" for c in plan.changes):
            return current
        entries = _fit(entries)
        result = write_memory(
            db,
            user,
            MemoryUpdate(
                expected_revision=current["revision"],
                enabled=current["enabled"],
                auto_save=current["auto_save"],
                entries=entries,
            ),
            "nightly",
            tombstone_removed=False,  # trimming old invalid history is housekeeping, not the user deleting a fact
        )
        return result


def _finish(db: Session, run: MemoryDreamRun, status: str, **fields) -> None:
    run.status = status
    for key, value in fields.items():
        setattr(run, key, value)
    run.finished_at = utcnow()
    run.claimed_until = None
    db.add(run)
    db.commit()


async def process_dream(run_id: str) -> None:
    """Read/claim and persist input, await models without a transaction, then fenced short commit."""
    token = new_id("claim")
    with SessionLocal() as db:
        now = utcnow()
        changed = db.execute(
            update(MemoryDreamRun)
            .where(
                MemoryDreamRun.id == run_id,
                MemoryDreamRun.status.in_(("pending", "running")),
                or_(MemoryDreamRun.claimed_until.is_(None), MemoryDreamRun.claimed_until < now),
            )
            .values(status="running", claim_token=token, claimed_until=now + timedelta(minutes=10), started_at=now)
        ).rowcount
        db.commit()
        if not changed:
            return
    try:
        with SessionLocal() as db:
            run = db.get(MemoryDreamRun, run_id)
            user = db.get(User, run.user_id)
            if not user or user.disabled_at:
                raise DreamError("账号不可用")
            with user_critical_section(db, user.id):
                db.refresh(user)
                memory = _memory_view(user)
                if not memory["enabled"] or not memory["auto_save"] or not dream_settings(user).enabled:
                    _finish(db, run, "skipped", journal="自动记忆已关闭")
                    return
                runs = _new_runs(db, user.id, _last_success(db, user.id), utcnow())
                if not runs:
                    _finish(db, run, "skipped", journal="没有新的对话")
                    return
                # A batch never combines evidence from different projects.
                project_id = run_project(db, runs[0])
                runs = [r for r in runs if run_project(db, r) == project_id]
                pending = [r for r in runs if (item := db.get(MemoryWorkItem, r.id)) is not None and item.state == "pending"]
                if pending:
                    runs = pending
                episodes = _fit_episodes(_episodes(db, user, runs), settings.memory_dream_input_chars)
                selected = {e.run_id for e in episodes}
                runs = [r for r in runs if r.id in selected]
                work = {r.id: db.get(MemoryWorkItem, r.id) for r in runs}
                cached = all(w is not None and w.state == "pending" for w in work.values())
                facts = [f for w in work.values() if w for f in w.candidates or []] if cached else None
                active = [e for e in _active(memory["entries"]) if e.get("project_id") == project_id]
                before = memory
                uid = user.id
                db.commit()  # Crucial: no SQL writer or user lock crosses either model await.

        usage = {}
        model = settings.memory_dream_model or get_catalog().default_id()
        context = dream_call.set((run_id, token))
        try:
            if facts is None:
                facts, usage, model = await _extract(episodes, active, user)
                for fact in facts:
                    fact["project_id"] = project_id
                with SessionLocal() as db:
                    with user_critical_section(db, uid):
                        owner = db.get(User, uid, populate_existing=True)
                        _validate_sources(db, owner, runs, project_id)
                        facts = _screen(facts, tombstones_of(owner))
                        _check_claim(db, run_id, token)
                        for source in runs:
                            item = db.get(MemoryWorkItem, source.id)
                            if item is None:
                                item = MemoryWorkItem(run_id=source.id, user_id=uid)
                            item.candidates = [f for f in facts if source.id in f.get("evidence_run_ids", [])]
                            item.state = "pending"
                            episode = next(e for e in episodes if e.run_id == source.id)
                            item.source_end = (
                                json.loads(episode.digest).get("source_end", 0)
                                if episode.digest.startswith('{"user_said":')
                                else len(source.prompt)
                            )
                            db.add(item)
                        db.commit()
            verdicts, judge_usage = await _judge(facts, active, user, model) if facts else ({}, {})
        finally:
            dream_call.reset(context)
        usage = {k: int(usage.get(k, 0)) + int(judge_usage.get(k, 0)) for k in ("prompt_tokens", "completion_tokens", "cached_tokens")}
        changes, consumed = [], []
        seen = {_hash(e["content"]) for e in active}
        for index, fact in enumerate(facts):
            verdict = verdicts.get(index, {})
            if verdict.get("deferred"):
                break
            if verdict.get("supported") is False:
                consumed.append(_hash(fact["text"]))
                continue
            if not verdict.get("duplicate_ids") and _hash(fact["text"]) not in seen:
                changes.append(
                    DreamChange(
                        op="add",
                        kind=fact.get("kind", "memory"),
                        content=fact["text"],
                        confidence=fact["confidence"],
                        evidence_run_ids=fact["evidence_run_ids"],
                        evidence_episode_ids=fact["evidence_episode_ids"],
                        entities=fact["entities"],
                        source_quote=fact["source_quote"],
                        project_id=project_id,
                        supersedes=verdict.get("contradicted_ids", []),
                        reason="extracted from user evidence",
                    )
                )
                seen.add(_hash(fact["text"]))
            consumed.append(_hash(fact["text"]))
            if len(changes) >= DREAM_MAX_CHANGES:
                break
        plan = DreamPlan(changes=changes, journal=f"保留 {len(changes)} 组变更")
        with SessionLocal() as db:
            with user_critical_section(db, uid):
                run = _check_claim(db, run_id, token)
                owner = db.get(User, uid, populate_existing=True)
                _validate_sources(db, owner, runs, project_id)
                if _memory_view(owner)["revision"] != before["revision"]:
                    raise DreamError("记忆已更新，稍后重试")
                after = (
                    _apply(db, owner, plan, {e.run_id: e for e in episodes}, expected_revision=before["revision"])
                    if changes
                    else _memory_view(owner)
                )
                for source in runs:
                    item = db.get(MemoryWorkItem, source.id)
                    if item is None:
                        item = MemoryWorkItem(run_id=source.id, user_id=uid)
                    item.candidates = [f for f in (item.candidates or []) if _hash(f["text"]) not in consumed]
                    if item.candidates:
                        item.state = "pending"
                    else:
                        item.source_offset = item.source_end or len(source.prompt)
                        item.state = "done" if item.source_offset >= len(_source_text(db, source)) else "ready"
                    item.updated_at = utcnow()
                    db.add(item)
                _finish(
                    db,
                    run,
                    "applied",
                    cursor_run_id=runs[-1].id,
                    input_run_ids=[r.id for r in runs],
                    plan=plan.model_dump(mode="json"),
                    journal=plan.journal,
                    before_memory=before,
                    after_memory=after,
                    token_usage=usage,
                )
    except Exception as exc:
        log.warning("memory dream stopped run=%s type=%s", run_id, type(exc).__name__)
        with SessionLocal() as db:
            run = db.get(MemoryDreamRun, run_id)
            if run and run.claim_token == token:
                _finish(db, run, "failed", error="记忆整理未完成，请稍后重试" if not isinstance(exc, DreamError) else str(exc)[:300])


def _validate_sources(db: Session, owner: User, runs: list[Run], project_id: str | None) -> None:
    document = _memory_view(owner)
    if owner.disabled_at or not document["enabled"] or not document["auto_save"] or not dream_settings(owner).enabled:
        raise DreamError("自动记忆已关闭")
    for old in runs:
        source = db.get(Run, old.id, populate_existing=True)
        conv = db.get(Conversation, source.conversation_id, populate_existing=True) if source and source.conversation_id else None
        if (
            not source
            or source.superseded_by_run_id
            or (conv and conv.deleted_at)
            or not run_permissions(db, source)["write"]
            or run_project(db, source) != project_id
        ):
            raise DreamError("来源对话或范围已改变")


def _check_claim(db: Session, run_id: str, token: str) -> MemoryDreamRun:
    run = db.get(MemoryDreamRun, run_id, populate_existing=True)
    if not run or run.claim_token != token or run.status != "running" or _aware(run.claimed_until) < utcnow():
        raise DreamError("整理任务已由其他进程接手")
    return run


def recover_pending(now: datetime | None = None) -> list[str]:
    now = _aware(now)
    stale = now - timedelta(minutes=10)
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(MemoryDreamRun)
                .where(
                    MemoryDreamRun.status.in_(("pending", "running")),
                    MemoryDreamRun.created_at < stale,
                    or_(MemoryDreamRun.claimed_until.is_(None), MemoryDreamRun.claimed_until < now),
                )
                .order_by(MemoryDreamRun.created_at, MemoryDreamRun.id)
                .limit(100)
            )
        )
        for row in rows:
            if row.claimed_until and _aware(row.claimed_until) >= now:
                continue
            row.status = "pending"
            row.claimed_until = None
            db.add(row)
        db.commit()
        return [row.id for row in rows]


async def dream_forever() -> None:
    if not settings.memory_dream_enabled or settings.env == "test":
        return
    log.info("memory dream scheduler started")
    try:
        for run_id in await asyncio.to_thread(recover_pending):
            await process_dream(run_id)
    except Exception:
        log.exception("memory dream recovery failed")
    while True:
        try:
            recovered = await asyncio.to_thread(recover_pending)
            claimed = await asyncio.to_thread(claim_due)
            for run_id in dict.fromkeys([*recovered, *claimed]):
                await process_dream(run_id)
        except Exception:
            log.exception("memory dream dispatch failed")
        await asyncio.sleep(settings.memory_dream_interval_seconds)


def list_runs(db: Session, user_id: str, limit: int = 30) -> list[MemoryDreamRunOut]:
    rows = db.scalars(
        select(MemoryDreamRun).where(MemoryDreamRun.user_id == user_id).order_by(MemoryDreamRun.created_at.desc()).limit(limit)
    )
    return [_run_out(r) for r in rows]


def metrics(db: Session, user_id: str) -> dict:
    rows = list(
        db.scalars(select(MemoryDreamRun).where(MemoryDreamRun.user_id == user_id).order_by(MemoryDreamRun.created_at.desc()).limit(365))
    )
    applied = [r for r in rows if r.status == "applied"]
    tokens = sum(
        int((r.token_usage or {}).get("prompt_tokens", 0)) + int((r.token_usage or {}).get("completion_tokens", 0)) for r in applied
    )
    changes = sum(len((r.plan or {}).get("changes") or []) for r in applied)
    return {
        "runs": len(rows),
        "applied": len(applied),
        "failed": sum(1 for r in rows if r.status == "failed"),
        "skipped": sum(1 for r in rows if r.status == "skipped"),
        "tokens": tokens,
        "changes": changes,
        "avg_changes": round(changes / len(applied), 2) if applied else 0,
    }
