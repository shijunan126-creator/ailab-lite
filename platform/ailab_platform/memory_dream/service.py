"""One bounded document rewrite per batch, retaining durable scheduling, leases, accounting and undo."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..catalog import get_catalog
from ..config import settings
from ..contracts.memory import (
    DreamChange,
    MemoryDreamRunOut,
)
from ..db import SessionLocal
from ..llm import llm_for
from ..locks import user_critical_section
from ..memory_recall.policy import project_mode, run_permissions, run_project, visible
from ..metrics import record_prompt_cache
from ..models import Conversation, MemoryDreamRun, MemoryEpisode, MemoryWorkItem, PlatformSetting, Run, RunStatus, User, new_id, utcnow
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


def active_job(now: datetime | None = None):
    """The chain owns the user slot until its successor or final report is committed."""
    return or_(
        MemoryDreamRun.status.in_(("pending", "running")),
        MemoryDreamRun.claimed_until > _aware(now),
        MemoryDreamRun.report["chain_running"].as_boolean().is_(True),
    )


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
    report = dict(run.report or {})
    if db and report.get("candidates"):
        from ..api.source_lifecycle import invalid_sources_query

        invalid = db.scalar(
            select(Run.id).where(Run.id.in_(run.input_run_ids or []), Run.id.in_(invalid_sources_query(run.user_id))).limit(1)
        )
        if invalid:
            report["candidates"] = []
    if "candidates" in report:
        report["candidates"] = [{k: v for k, v in entry.items() if k != "evidence_run_ids"} for entry in report["candidates"]]
    report["runs_seen"] = int(report.get("runs_seen", len(run.input_run_ids or []))) + int(report.get("runs_seen_before", 0))
    report["added"] = int(report.get("added", 0)) + int(report.get("added_before", 0))
    return MemoryDreamRunOut(
        id=run.id,
        status="running" if report.get("chain_running") and not report.get("next_id") else run.status,
        trigger=run.trigger,
        local_date=run.local_date,
        timezone=run.timezone,
        cursor_run_id=run.cursor_run_id,
        input_run_ids=run.input_run_ids or [],
        changes=changes,
        journal=run.journal or "",
        report=report,
        token_usage=run.token_usage or {},
        error=run.error or "",
        created_at=_aware(run.created_at) if run.created_at else None,
        finished_at=_aware(run.finished_at) if run.finished_at else None,
    )


def _last_success(db: Session, user_id: str) -> MemoryDreamRun | None:
    return db.scalar(
        select(MemoryDreamRun)
        .where(
            MemoryDreamRun.user_id == user_id,
            or_(
                MemoryDreamRun.status.in_(("applied", "reverted")),
                (MemoryDreamRun.status == "skipped") & MemoryDreamRun.report["dreaming"]["expired"].as_integer().is_not(None),
            ),
        )
        .order_by(MemoryDreamRun.finished_at.desc(), MemoryDreamRun.created_at.desc())
        .limit(1)
    )


def _new_runs(
    db: Session,
    user_id: str,
    last: MemoryDreamRun | None,
    now: datetime,
    limit: int = 30,
    *,
    project_id: str | None = None,
    scoped: bool = False,
) -> list[Run]:
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
                or_(not scoped, Conversation.project_id == project_id)
                if project_id
                else or_(not scoped, Conversation.project_id.is_(None)),
                Run.options["evaluation"].as_boolean().is_not(True),
                Run.options["eval"].as_boolean().is_not(True),
                Run.runtime_meta["evaluation"].as_boolean().is_not(True),
                Run.id.not_in(invalid_sources_query(user_id)),
                Run.status.in_(TERMINAL),
                ~processed,
                Conversation.memory_policy["write"].as_boolean().is_not(False),
                Run.options["memory_policy"]["write"].as_boolean().is_not(False),
                Run.superseded_by_run_id.is_(None),  # the user rewrote this turn; what they replaced is not what they meant
                or_(Run.conversation_id.is_(None), Conversation.deleted_at.is_(None)),  # a deleted conversation is not evidence
            )
            .order_by(Run.created_at, Run.id)
            .limit(limit)
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
            if not ds.enabled or not document["enabled"]:
                continue
            zone_name = ds.timezone or settings.memory_dream_default_timezone
            try:
                zone = ZoneInfo(zone_name)
            except Exception:
                zone = ZoneInfo(settings.memory_dream_default_timezone)
            local = now.astimezone(zone)
            start, end = local_window(ds)
            from .scheduling import offset

            if local.hour * 60 + local.minute < start + offset(user.id, end - start):
                continue
            runs = _new_runs(db, user.id, _last_success(db, user.id), now) if document["auto_save"] else []
            from ..memory_recall.document import used_chars

            scopes = {e.get("project_id") for e in document["entries"]}
            maintenance = any(
                used_chars([e for e in document["entries"] if e.get("project_id") == pid]) > settings.memory_doc_max_chars for pid in scopes
            )
            if not runs and not maintenance:
                continue
            local_date = local.date().isoformat()
            active = db.scalar(select(MemoryDreamRun.id).where(MemoryDreamRun.user_id == user.id, active_job(now)).limit(1))
            count = len(
                list(
                    db.scalars(
                        select(MemoryDreamRun.id)
                        .where(MemoryDreamRun.user_id == user.id, MemoryDreamRun.local_date == local_date, MemoryDreamRun.trigger == "cron")
                        .limit(settings.memory_dream_auto_batches)
                    )
                )
            )
            if active or count >= settings.memory_dream_auto_batches:
                continue
            row = MemoryDreamRun(
                id=new_id("dream"),
                user_id=user.id,
                local_date=local_date,
                timezone=zone_name,
                status="pending",
                trigger="cron",
                input_run_ids=[r.id for r in runs],
                token_usage={},
            )
            backfill = bool(runs) and any((item := db.get(MemoryWorkItem, r.id)) is not None and item.state == "backfill" for r in runs)
            row.kind = "dream"
            row.priority = 20 if backfill else 10
            row.due_at = local.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0).astimezone(UTC) + timedelta(
                minutes=offset(user.id, end - start)
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


def _assistant_context(value: str) -> str:
    value = re.sub(r"```[^\n]*\n.*?(?:```|$)", "", value, flags=re.S)
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value)
    return value.strip()[:300]


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
                "assistant_replied": _assistant_context(run.summary or ""),
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

        def reserve():
            with SessionLocal() as db:
                claimed = _check_claim(db, identity[0], identity[1])
                claimed.claimed_until = utcnow() + timedelta(minutes=10)
                db.commit()
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

        await asyncio.to_thread(reserve)
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

            def settle():
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

            await asyncio.to_thread(settle)
        if call.usage:
            from .scheduling import account_tokens

            def account():
                with SessionLocal() as db:
                    job = db.get(MemoryDreamRun, identity[0]) if identity else None
                    account_tokens(db, call.usage, job.kind if job else "extract", utcnow())

            if identity:
                await asyncio.to_thread(account)
            record_prompt_cache("memory", "memory", call.usage)
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


def _finish(db: Session, run: MemoryDreamRun, status: str, **fields) -> None:
    """Commit the batch outcome and cursor together, independently of derived summary work."""
    report = dict(fields.get("report") or run.report or {})
    report.pop("_final_status", None)
    report["chain_running"] = run.trigger != "edit"
    report["stage_committed"] = True
    fields["report"] = report
    run.status = "applied" if report.get("merged_projects") else status
    run.claimed_until = utcnow() + timedelta(minutes=10)
    for key, value in fields.items():
        setattr(run, key, value)
    run.finished_at = utcnow()
    from ..metrics import MEMORY_DREAM_JOBS, MEMORY_DREAM_SECONDS

    kind = run.kind if run.kind in {"extract", "dream", "edit", "merge"} else "extract"
    MEMORY_DREAM_JOBS.labels(kind, run.status if run.status in {"applied", "failed", "skipped"} else "other").inc()
    if run.started_at:
        MEMORY_DREAM_SECONDS.labels(kind).observe(max(0, (run.finished_at - _aware(run.started_at)).total_seconds()))
    db.add(run)
    db.commit()


async def process_dream(run_id: str) -> None:
    """One fenced document rewrite call per batch; no SQL transaction crosses the await."""
    token = new_id("claim")

    def prepare():
        from ..memory_recall.document import active, used_chars
        from .scheduling import admission, can_start

        with SessionLocal() as db, admission(db) as scheduler:
            job = db.get(MemoryDreamRun, run_id)
            now = utcnow()
            if not job or job.status not in {"pending", "running"} or _aware(job.due_at) > now:
                return
            if job.claimed_until and _aware(job.claimed_until) > now:
                return
            if not can_start(db, scheduler, job, now):
                return
            job.status, job.claim_token = "running", token
            job.claimed_until = now + timedelta(minutes=10)
            job.started_at = now
            owner = db.get(User, job.user_id)
            if not owner or owner.disabled_at:
                _finish(db, job, "skipped")
                return
            with user_critical_section(db, owner.id):
                document = _memory_view(owner)
                if not document["enabled"]:
                    _finish(db, job, "skipped")
                    return
                if job.trigger == "edit":
                    db.commit()
                    return ("edit",)
                runs = (
                    _new_runs(db, owner.id, None, now, project_id=job.project_id, scoped=job.trigger == "manual")
                    if document["auto_save"]
                    else []
                )
                pid = run_project(db, runs[0]) if runs else job.project_id
                if not runs and job.trigger == "cron":
                    pid = next(
                        (
                            scope
                            for scope in {e.get("project_id") for e in document["entries"]}
                            if used_chars([e for e in document["entries"] if e.get("project_id") == scope]) > settings.memory_doc_max_chars
                        ),
                        pid,
                    )
                mode = project_mode(db, pid)
                runs = [x for x in runs if run_project(db, x) == pid]
                scope = pid if mode == "isolated" else None
                entries = active([e for e in document["entries"] if e.get("project_id") == scope])
                if not runs and used_chars(entries) <= settings.memory_doc_max_chars:
                    _finish(db, job, "skipped", report={"runs_seen": 0, "added": 0})
                    job.claimed_until = None
                    db.commit()
                    return
                episodes = _fit_episodes(_episodes(db, owner, runs), settings.memory_dream_input_chars) if runs else []
                selected = {e.run_id for e in episodes}
                runs = [x for x in runs if x.id in selected]
                db.commit()
                return owner, runs, episodes, entries, scope, pid, mode

    snapshot = await asyncio.to_thread(prepare)
    if not snapshot:
        return
    identity = dream_call.set((run_id, token))
    try:
        if snapshot == ("edit",):
            from .maintenance import edit

            await edit(run_id, token)
            return
        owner, runs, episodes, entries, scope, pid, mode = snapshot
        from .document import apply_ops, messages

        raw, usage = await _chat(messages(episodes, entries), settings.memory_dream_model or get_catalog().default_id(), owner)
        result = _json(raw)
        if not isinstance(result.get("ops"), list):
            raise DreamError("整理模型未返回有效ops")

        def apply():
            from ..api.routes_memory import memory_for
            from .changes import digest, record
            from .maintenance import save

            with SessionLocal() as db, user_critical_section(db, owner.id):
                job = _check_claim(db, run_id, token)
                user = db.get(User, owner.id, populate_existing=True)
                _validate_sources(db, user, runs, pid, expected_mode=mode, manual=job.trigger == "manual")
                current = memory_for(user)
                shown = [
                    e for e in current["entries"] if e.get("project_id") == scope and visible(e, scope, mode="isolated", standing=True)
                ]
                if digest(shown) != digest(entries):
                    raise DreamError("记忆已变化，请重试")
                changed, audit = apply_ops(current["entries"], entries, episodes, result.get("ops", []), user, scope)
                after = save(db, user, current, changed)
                record(job, current, after)
                for episode in episodes:
                    item = db.get(MemoryWorkItem, episode.run_id)
                    if item is None:
                        item = MemoryWorkItem(run_id=episode.run_id, user_id=user.id)
                    source = db.get(Run, episode.run_id)
                    item.source_offset = json.loads(episode.digest).get("source_end", len(source.prompt))
                    item.source_end = item.source_offset
                    item.state = "done" if item.source_offset >= len(_source_text(db, source)) else "ready"
                    item.candidates = []
                    db.add(item)
                _finish(
                    db,
                    job,
                    "applied",
                    input_run_ids=[e.run_id for e in episodes],
                    token_usage=usage,
                    report={"runs_seen": len(episodes), "added": sum(x["op"] == "add" for x in audit), "ops": audit},
                    cursor_run_id=runs[-1].id if runs else None,
                )

        await asyncio.to_thread(apply)
    except Exception as exc:
        log.warning("memory document batch failed type=%s", type(exc).__name__)

        def fail(error):
            with SessionLocal() as db:
                job = db.get(MemoryDreamRun, run_id)
                if job and job.claim_token == token and job.status == "running":
                    _finish(db, job, "failed", error=str(error)[:300] if isinstance(error, DreamError) else "记忆整理未完成，请稍后重试")

        await asyncio.to_thread(fail, exc)
    finally:

        def release():
            with SessionLocal() as db:
                db.execute(
                    update(MemoryDreamRun)
                    .where(MemoryDreamRun.id == run_id, MemoryDreamRun.claim_token == token)
                    .values(claimed_until=None)
                )
                db.commit()

        await asyncio.to_thread(release)
        dream_call.reset(identity)


async def _process_dream_chain(run_id: str) -> None:
    """Process bounded successive batches, creating each only after the previous commit."""

    def batch_limit():
        with SessionLocal() as db:
            first = db.get(MemoryDreamRun, run_id)
            if not first:
                return
            if first.trigger == "edit":
                return 1, 1
            limit = settings.memory_dream_manual_batches if first.trigger == "manual" else settings.memory_dream_auto_batches
            remaining = max(1, limit - first.batch_index + 1) if first.trigger == "manual" else limit
        return remaining, limit

    limits = await asyncio.to_thread(batch_limit)
    if limits is None:
        return
    remaining, limit = limits
    for batch in range(remaining):
        await process_dream(run_id)

        def next_batch():
            with SessionLocal() as db:
                previous = db.get(MemoryDreamRun, run_id)
                if not previous or previous.status in {"pending", "running"}:
                    return
                if (previous.report or {}).get("next_id"):
                    return previous.report["next_id"]
                if (previous.report or {}).get("chain_complete"):
                    return

                with user_critical_section(db, previous.user_id):
                    db.refresh(previous)
                    if (previous.report or {}).get("next_id"):
                        return previous.report["next_id"]
                    if (previous.report or {}).get("chain_complete"):
                        return
                    owner = db.get(User, previous.user_id, populate_existing=True)

                    def publish():
                        previous.report = {**(previous.report or {}), "chain_running": False, "chain_complete": True}
                        db.commit()

                    if previous.status != "applied" or (previous.trigger != "cron" and previous.batch_index >= limit):
                        publish()
                        return
                    if (
                        not owner
                        or not _memory_view(owner)["auto_save"]
                        or not _new_runs(
                            db,
                            previous.user_id,
                            None,
                            utcnow(),
                            limit=1,
                            project_id=previous.project_id,
                            scoped=previous.trigger == "manual",
                        )
                    ):
                        publish()
                        return
                    if db.scalar(
                        select(MemoryDreamRun.id)
                        .where(MemoryDreamRun.user_id == previous.user_id, MemoryDreamRun.id != previous.id, active_job())
                        .limit(1)
                    ):
                        return
                    carry = False
                    if previous.trigger == "cron":
                        ds = dream_settings(owner)
                        local = utcnow().astimezone(ZoneInfo(ds.timezone or settings.memory_dream_default_timezone))
                        start, end = local_window(ds)
                        if not ds.enabled:
                            publish()
                            return
                        carry = (
                            not start <= local.hour * 60 + local.minute < end
                            or local.date().isoformat() != previous.local_date
                            or previous.batch_index >= limit
                        )
                        used = len(
                            list(
                                db.scalars(
                                    select(MemoryDreamRun.id)
                                    .where(
                                        MemoryDreamRun.user_id == previous.user_id,
                                        MemoryDreamRun.local_date == previous.local_date,
                                        MemoryDreamRun.trigger == "cron",
                                    )
                                    .limit(limit)
                                )
                            )
                        )
                        carry = carry or used >= limit
                    row = MemoryDreamRun(
                        id=new_id("dream"),
                        user_id=previous.user_id,
                        local_date=previous.local_date,
                        timezone=previous.timezone,
                        status="pending",
                        trigger=previous.trigger,
                        project_id=previous.project_id,
                        batch_index=1 if carry else previous.batch_index + 1,
                        priority=previous.priority,
                        due_at=previous.due_at,
                        kind=previous.kind,
                        report={
                            "runs_seen_before": (previous.report or {}).get("runs_seen_before", 0)
                            + (previous.report or {}).get("runs_seen", 0),
                            "added_before": (previous.report or {}).get("added_before", 0) + (previous.report or {}).get("added", 0),
                        },
                        input_run_ids=[],
                        token_usage={},
                    )
                    db.add(row)
                    previous.report = {**(previous.report or {}), "next_id": row.id, "chain_running": False, "chain_complete": True}
                    try:
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                        return
                    return None if carry else row.id

        run_id = await asyncio.to_thread(next_batch)
        if run_id is None:
            return


def _validate_sources(
    db: Session, owner: User, runs: list[Run], project_id: str | None, *, expected_mode: str | None = None, manual: bool = False
) -> None:
    if expected_mode and project_mode(db, project_id) != expected_mode:
        raise DreamError("来源项目的记忆模式已改变")
    document = _memory_view(owner)
    if (
        owner.disabled_at
        or not document["enabled"]
        or (runs and not document["auto_save"])
        or (not manual and not dream_settings(owner).enabled)
    ):
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
                    or_(MemoryDreamRun.status.in_(("pending", "running")), MemoryDreamRun.report["chain_running"].as_boolean().is_(True)),
                    or_(MemoryDreamRun.status == "pending", MemoryDreamRun.created_at < stale),
                    MemoryDreamRun.due_at <= now,
                    or_(MemoryDreamRun.claimed_until.is_(None), MemoryDreamRun.claimed_until < now),
                )
                .order_by(MemoryDreamRun.priority, MemoryDreamRun.due_at, MemoryDreamRun.id)
                .limit(100)
            )
        )
        for row in rows:
            if row.claimed_until and _aware(row.claimed_until) >= now:
                continue
            if row.status in {"pending", "running"}:
                row.status = "pending"
            row.claimed_until = None
            db.add(row)
        db.commit()
        return [row.id for row in rows]


async def dream_forever() -> None:
    if not settings.memory_dream_enabled or settings.env == "test":
        return
    log.info("memory dream scheduler started")
    while True:
        try:
            recovered = await asyncio.to_thread(recover_pending)
            claimed = await asyncio.to_thread(claim_due)

            def ordered():
                from ..metrics import MEMORY_DREAM_QUEUE

                with SessionLocal() as db:
                    from sqlalchemy import func

                    MEMORY_DREAM_QUEUE.set(
                        db.scalar(select(func.count()).select_from(MemoryDreamRun).where(MemoryDreamRun.status == "pending"))
                    )
                    return list(
                        db.scalars(
                            select(MemoryDreamRun.id)
                            .where(MemoryDreamRun.id.in_([*recovered, *claimed]))
                            .order_by(MemoryDreamRun.priority, MemoryDreamRun.due_at, MemoryDreamRun.id)
                        )
                    )

            jobs = await asyncio.to_thread(ordered)

            def progress_snapshot():
                with SessionLocal() as db:
                    rows = db.execute(
                        select(
                            MemoryDreamRun.id,
                            MemoryDreamRun.status,
                            MemoryDreamRun.finished_at,
                            MemoryDreamRun.report["chain_complete"].as_boolean(),
                            MemoryDreamRun.report["dreaming"]["expired"].as_integer().is_not(None),
                        ).where(MemoryDreamRun.id.in_(jobs))
                    )
                    return {row[0]: tuple(row[1:]) for row in rows}

            before = await asyncio.to_thread(progress_snapshot)
            semaphore = asyncio.Semaphore(settings.memory_dream_concurrency)

            async def process(job):
                async with semaphore:
                    await process_dream_chain(job)

            await asyncio.gather(*(process(job) for job in jobs))
            after = await asyncio.to_thread(progress_snapshot)
            if after != before:
                # Drain ready backlog after progress, but retain the idle/backpressure
                # delay when every attempted admission was refused.
                def ready():
                    with SessionLocal() as db:
                        return db.scalar(
                            select(MemoryDreamRun.id)
                            .where(
                                MemoryDreamRun.status == "pending",
                                MemoryDreamRun.due_at <= utcnow(),
                                or_(MemoryDreamRun.claimed_until.is_(None), MemoryDreamRun.claimed_until < utcnow()),
                            )
                            .limit(1)
                        )

                if await asyncio.to_thread(ready):
                    continue
        except Exception:
            log.exception("memory dream dispatch failed")
        await asyncio.sleep(settings.memory_dream_interval_seconds)


def list_runs(db: Session, user_id: str, limit: int = 30) -> list[MemoryDreamRunOut]:
    rows = db.scalars(
        select(MemoryDreamRun).where(MemoryDreamRun.user_id == user_id).order_by(MemoryDreamRun.created_at.desc()).limit(limit)
    )
    out = []
    for row in rows:
        item = _run_out(row)
        item.report = {k: v for k, v in item.report.items() if k in {"scope", "counts", "pending_after"}}
        out.append(item)
    return out


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


async def process_dream_chain(run_id: str) -> None:
    await _process_dream_chain(run_id)
