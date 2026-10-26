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
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from rapidfuzz import fuzz
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..catalog import get_catalog
from ..config import settings
from ..contracts.memory import (
    DREAM_MAX_CHANGES,
    DREAM_TARGET_RATIO,
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
from ..memory_recall.tombstones import blocked_by_tombstone, tombstones_of
from ..metrics import MEMORY_GUARD_REJECTED
from ..models import Conversation, MemoryDreamRun, MemoryEpisode, Run, RunStatus, User, new_id, utcnow
from .prompts import EXTRACT_SYSTEM, JUDGE_SYSTEM
from .settings import dream_settings, local_window

log = logging.getLogger("ailab.memory_dream")

TERMINAL = set(RunStatus.TERMINAL)


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
    return [e for e in entries if not e.get("superseded_by") and not e.get("invalid_at")]


def _run_out(run: MemoryDreamRun) -> MemoryDreamRunOut:
    plan = run.plan or {}
    changes = []
    for item in plan.get("changes") or []:
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
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def _last_success(db: Session, user_id: str) -> MemoryDreamRun | None:
    return db.scalar(
        select(MemoryDreamRun)
        .where(MemoryDreamRun.user_id == user_id, MemoryDreamRun.status.in_(("applied", "reverted")))
        .order_by(MemoryDreamRun.finished_at.desc(), MemoryDreamRun.created_at.desc())
        .limit(1)
    )


def _new_runs(db: Session, user_id: str, last: MemoryDreamRun | None, now: datetime) -> list[Run]:
    since = now - timedelta(hours=24)
    if last:
        cursor = db.get(Run, last.cursor_run_id) if last.cursor_run_id else None
        since = _aware(cursor.created_at) if cursor else _aware(last.finished_at or last.created_at)
    return list(
        db.scalars(
            select(Run)
            .outerjoin(Conversation, Conversation.id == Run.conversation_id)
            .where(
                Run.user_id == user_id,
                Run.status.in_(TERMINAL),
                Run.created_at > since,
                Run.superseded_by_run_id.is_(None),  # the user rewrote this turn; what they replaced is not what they meant
                or_(Run.conversation_id.is_(None), Conversation.deleted_at.is_(None)),  # a deleted conversation is not evidence
            )
            .order_by(Run.created_at, Run.id)
            .limit(40)
        )
    )


def _candidate_users(db: Session, now: datetime) -> list[User]:
    return list(db.scalars(select(User).where(User.disabled_at.is_(None))).all())


def claim_due(now: datetime | None = None) -> list[str]:
    now = _aware(now)
    claimed: list[str] = []
    with SessionLocal() as db:
        for user in _candidate_users(db, now):
            ds = dream_settings(user)
            if not ds.enabled:
                continue
            zone_name = ds.timezone or settings.memory_dream_default_timezone
            try:
                zone = ZoneInfo(zone_name)
            except Exception:
                zone = ZoneInfo(settings.memory_dream_default_timezone)
            local = now.astimezone(zone)
            start, end = local_window(ds, zone_name)
            if not start <= local.hour * 60 + local.minute < end:
                continue
            if db.scalar(select(Run.id).where(Run.user_id == user.id, Run.status.notin_(TERMINAL)).limit(1)):
                continue
            runs = _new_runs(db, user.id, _last_success(db, user.id), now)
            if len(runs) < settings.memory_dream_min_runs:
                continue
            local_date = local.date().isoformat()
            if db.scalar(
                select(MemoryDreamRun.id).where(
                    MemoryDreamRun.user_id == user.id,
                    MemoryDreamRun.local_date == local_date,
                )
            ):
                continue
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
    head = digest.split(ASSISTANT_MARK, 1)[0]
    return head.removeprefix("用户：")


def _episodes(db: Session, user: User, runs: list[Run]) -> list[MemoryEpisode]:
    """Create immutable episode rows once; reuse them on a retry."""
    existing = {e.content_hash: e for e in db.scalars(select(MemoryEpisode).where(MemoryEpisode.user_id == user.id)).all()}
    out: list[MemoryEpisode] = []
    for run in runs:
        digest = _digest(run)
        content_hash = _hash(digest)
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
                "assistant_replied": e.digest.split(ASSISTANT_MARK, 1)[1] if ASSISTANT_MARK in e.digest else "",
            }
            for e in episodes
        ],
        ensure_ascii=False,
    )


def _quote_supported(quote: str, episodes: list[MemoryEpisode]) -> bool:
    """A quote counts only if the *user* said it: assistant text is context, never evidence (doc §7.2)."""
    q = _normalize(quote)
    return bool(q) and any(q in _normalize(_user_part(e.digest)) for e in episodes)


async def _chat(messages: list[dict], model: str, user: User) -> tuple[str, dict]:
    timeout = settings.llm_total_timeout_seconds or settings.llm_timeout_seconds
    async with asyncio.timeout(timeout):
        response = await llm_for(model).chat(messages, [], model, user_id=user.id)
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
    user_prompt = (
        "已有事实（用于避免重复；不要从这些事实里再抽取）：\n"
        + json.dumps(existing, ensure_ascii=False)
        + "\n\n新 episodes：\n"
        + _episode_payload(episodes)
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
    episode_ids = {e.id for e in episodes}
    run_ids = {e.run_id for e in episodes if e.run_id}
    out: list[dict] = []
    for item in facts[: DREAM_MAX_CHANGES * 2]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        quote = str(item.get("source_quote") or "").strip()
        evidence = {str(x) for x in item.get("evidence_run_ids") or [] if str(x) in run_ids}
        episode_evidence = {str(x) for x in item.get("evidence_episode_ids") or [] if str(x) in episode_ids}
        if not text or not quote or not _quote_supported(quote, episodes):
            continue
        if len(text) > 1000 or len(quote) > 1000:
            continue
        if evidence and not (evidence <= run_ids):
            continue
        if not evidence:
            evidence = {e.run_id for e in episodes if e.run_id and _quote_supported(quote, [e])}
        if not episode_evidence:
            episode_evidence = {e.id for e in episodes if _quote_supported(quote, [e])}
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        if confidence < settings.memory_dream_confidence:
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
        candidates = _candidates(fact, active)
        if not candidates:
            continue
        work.append(
            {
                "index": index,
                "new": {"text": fact["text"], "source_quote": fact["source_quote"]},
                "existing": [{"id": c["id"], "text": c["content"], "valid_from": c.get("valid_from")} for c in candidates],
            }
        )
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
    out: dict[int, dict] = {}
    for item in results:
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if index not in by_index:
            continue
        valid_ids = {c["id"] for c in by_index[index]["existing"]}
        out[index] = {
            "duplicate_ids": [x for x in item.get("duplicate_ids") or [] if x in valid_ids],
            "contradicted_ids": [x for x in item.get("contradicted_ids") or [] if x in valid_ids],
        }
    return out, usage


def _fit(entries: list[MemoryEntryInput]) -> list[MemoryEntryInput]:
    """Keep active facts and recent invalid history inside the JSON budget."""
    total = sum(len(e.content) for e in entries)
    if total <= int(MAX_CHARS * DREAM_TARGET_RATIO):
        return entries
    # Drop the oldest invalidated facts first; active facts are never silently dropped.
    invalid = sorted(
        [e for e in entries if e.invalid_at or e.superseded_by],
        key=lambda e: e.invalid_at or datetime.min.replace(tzinfo=UTC),
    )
    for item in invalid:
        if total <= int(MAX_CHARS * DREAM_TARGET_RATIO):
            break
        total -= len(item.content)
        entries = [e for e in entries if e is not item]
    if total > int(MAX_CHARS * DREAM_TARGET_RATIO):
        raise DreamError("active memory exceeds the dream budget target")
    return entries


def _apply(db: Session, user: User, plan: DreamPlan, episode_by_run: dict[str, MemoryEpisode]) -> dict:
    from ..api.routes_memory import memory_for, write_memory

    with user_critical_section(db, user.id):
        db.refresh(user)
        current = memory_for(user)
        if not current["enabled"]:
            raise DreamError("memory disabled")
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
            )
            for e in current["entries"]
        ]
        by_id = {e.id: e for e in entries}
        added = 0
        now = utcnow()
        for change in plan.changes:
            if change.op == "add":
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
                    )
                )
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
    db.add(run)
    db.commit()


async def process_dream(run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(MemoryDreamRun, run_id, populate_existing=True)
        if not run or run.status not in {"pending", "running"}:
            return
        user = db.get(User, run.user_id, populate_existing=True)
        if not user:
            _finish(db, run, "failed", error="user not found")
            return
        run.status = "running"
        run.started_at = utcnow()
        db.commit()
        try:
            last = _last_success(db, user.id)
            runs = _new_runs(db, user.id, last, utcnow())
            if len(runs) < settings.memory_dream_min_runs:
                _finish(db, run, "skipped", journal="没有足够的新对话")
                return
            memory = _memory_view(user)
            if not memory["enabled"]:
                _finish(db, run, "skipped", journal="用户关闭了记忆")
                return
            episodes = _episodes(db, user, runs)
            active = _active(memory["entries"])
            facts, usage, model = await _extract(episodes, active, user)
            facts = _screen(facts, tombstones_of(user))
            verdicts, judge_usage = await _judge(facts, active, user, model) if facts else ({}, {})
            usage = {
                key: int(usage.get(key, 0)) + int(judge_usage.get(key, 0))
                for key in ("prompt_tokens", "completion_tokens", "cached_tokens")
            }
            changes: list[DreamChange] = []
            episode_by_run = {e.run_id: e for e in episodes if e.run_id}
            seen_text = {_hash(e["content"]) for e in active}
            for index, fact in enumerate(facts):
                verdict = verdicts.get(index, {})
                if verdict.get("duplicate_ids"):
                    continue
                if _hash(fact["text"]) in seen_text:
                    continue
                seen_text.add(_hash(fact["text"]))
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
                        reason="extracted from episode",
                    )
                )
                for old_id in verdict.get("contradicted_ids", []):
                    changes.append(DreamChange(op="invalidate", id=old_id, reason="contradicted by new fact"))
                if len(changes) >= DREAM_MAX_CHANGES:
                    break
            plan = DreamPlan(changes=changes[:DREAM_MAX_CHANGES], journal=f"抽取 {len(facts)} 条候选，保留 {len(changes)} 条变更")
            before = memory
            after = _apply(db, user, plan, episode_by_run) if plan.changes else memory
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
            log.warning("memory dream failed run=%s: %s", run.id, exc)
            db.rollback()
            run = db.get(MemoryDreamRun, run_id, populate_existing=True)
            if run:
                _finish(db, run, "failed", error=f"{type(exc).__name__}: {exc}"[:1000])


def recover_pending(now: datetime | None = None) -> list[str]:
    now = _aware(now)
    stale = now - timedelta(minutes=10)
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(MemoryDreamRun).where(
                    MemoryDreamRun.status.in_(("pending", "running")),
                    MemoryDreamRun.created_at < stale,
                )
            )
        )
        for row in rows:
            row.status = "pending"
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
            claimed = await asyncio.to_thread(claim_due)
            for run_id in claimed:
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
    rows = list(db.scalars(select(MemoryDreamRun).where(MemoryDreamRun.user_id == user_id)).all())
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
