"""Bounded user schedules; occurrences and run outbox share one transaction."""

import asyncio
import logging
import time
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from prometheus_client import Counter
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..catalog import get_catalog
from ..config import settings
from ..db import SessionLocal
from ..deps import current_user, get_db
from ..library import owned_project
from ..locks import user_critical_section
from ..models import Conversation, Notification, Run, RunDelivery, RunStatus, ScheduledTask, User, utcnow
from ..schemas import ORMModel
from ..service_auth import require_scope
from ..skill_policy import run_disabled_skills
from ..skill_registry import resolve_name
from .routes_runs import skills, start_run, usable_personal

router = APIRouter(prefix="/v1/schedules", tags=["schedules"])
EXECUTIONS = Counter("ailab_schedule_executions_total", "Schedule executions", ["status"])
log = logging.getLogger(__name__)
LIMIT = 5
UNREAD_OCCURRENCES = 3
UNREAD_GRACE = timedelta(days=7)
FAILURE_LIMIT = 3
# an occurrence that finds its conversation mid-turn retries this often, for at most this long
DEFER_RETRY = timedelta(minutes=1)
DEFER_LIMIT = timedelta(hours=2)
START_ERRORS = {403: "账号已停用", 404: "项目已删除", 409: "会话正在回答中", 410: "会话已删除", 429: "额度或并发任务已达上限"}


class Schedule(BaseModel):
    kind: Literal["daily", "weekdays", "weekly", "once"]
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    days: list[int] | None = None
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")  # only for once, in the task's timezone

    @model_validator(mode="after")
    def valid_days(self):
        if self.kind == "weekly":
            if not self.days or any(d < 0 or d > 6 for d in self.days) or len(set(self.days)) != len(self.days):
                raise ValueError("请选择有效星期")
        elif self.days is not None:
            raise ValueError("只有每周任务可指定星期")
        if self.kind == "once":
            try:
                date.fromisoformat(self.date or "")
            except ValueError:
                raise ValueError("请选择日期") from None
        elif self.date is not None:
            raise ValueError("只有单次任务可指定日期")
        return self


class NoTemplate(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_template(cls, value):
        if isinstance(value, dict) and value.get("template") is not None:
            raise ValueError("定时任务暂不支持模板")
        return value


class TaskInput(NoTemplate):
    title: str = Field(min_length=1, max_length=60)
    prompt: str = Field(min_length=1)
    skill: str | None = None
    model: str | None = None
    project_id: str | None = None
    schedule: Schedule
    timezone: str = Field(max_length=100)
    enabled: bool = True
    ends_on: date | None = None

    @model_validator(mode="after")
    def valid(self):
        if not self.title.strip() or not self.prompt.strip() or len(self.prompt) > settings.max_prompt_chars:
            raise ValueError("标题或提示词无效")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("时区无效") from None
        if self.schedule.kind == "once" and self.ends_on is not None:
            raise ValueError("单次任务没有结束日期")
        return self


class TaskPatch(NoTemplate):
    title: str | None = None
    prompt: str | None = None
    skill: str | None = None
    model: str | None = None
    project_id: str | None = None
    schedule: Schedule | None = None
    timezone: str | None = None
    enabled: bool | None = None
    ends_on: date | None = None


class TaskOut(TaskInput, ORMModel):
    mode: Literal["chat", "work"]
    paused_reason: str | None = None
    conversation_id: str | None = None
    conversation_title: str | None = None
    due_at: datetime | None = None
    id: str
    user_id: str
    next_run_at: datetime
    last_run_at: datetime | None
    last_run_id: str | None
    last_conversation_id: str | None
    last_status: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


def next_occurrence(schedule: dict, timezone: str, after: datetime) -> datetime | None:
    """Nonexistent wall times skip that day; overlaps fire once at the earlier fold. None: a single task already ran."""
    zone = ZoneInfo(timezone)
    after = after.replace(tzinfo=UTC) if after.tzinfo is None else after.astimezone(UTC)
    hour, minute = map(int, schedule["time"].split(":"))
    if schedule["kind"] == "once":
        day = date.fromisoformat(schedule["date"])
        local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=0)
        candidate = local.astimezone(UTC)
        exists = candidate.astimezone(zone).replace(tzinfo=None) == local.replace(tzinfo=None)
        # a single time that falls into a DST gap runs at the first valid minute after it
        return (candidate if exists else (local + timedelta(hours=1)).astimezone(UTC)) if candidate > after else None
    day = after.astimezone(zone).date()
    for offset in range(16):
        when = day + timedelta(days=offset)
        allowed = schedule["kind"] == "daily" or (
            when.weekday() < 5 if schedule["kind"] == "weekdays" else when.weekday() in schedule["days"]
        )
        if not allowed:
            continue
        local = datetime(when.year, when.month, when.day, hour, minute, tzinfo=zone, fold=0)
        candidate = local.astimezone(UTC)
        if candidate > after and candidate.astimezone(zone).replace(tzinfo=None) == local.replace(tzinfo=None):
            return candidate
    raise ValueError("无法计算下次执行时间")


def first_occurrence(schedule: dict, timezone: str, now: datetime) -> datetime:
    upcoming = next_occurrence(schedule, timezone, now)
    if upcoming is None:
        raise HTTPException(422, "时间已过，请选择以后的时间")
    return upcoming


DAY_NAMES = "一二三四五六日"


def rule_text(schedule: dict) -> str:
    """How people say it: 每天 09:00 / 工作日 09:00 / 每周一、三 09:00 / 11月12日 09:00."""
    kind, at = schedule["kind"], schedule["time"]
    if kind == "daily":
        return f"每天 {at}"
    if kind == "weekdays":
        return f"工作日 {at}"
    if kind == "weekly":
        return "每周" + "、".join(DAY_NAMES[d] for d in sorted(schedule.get("days") or [])) + f" {at}"
    day = date.fromisoformat(schedule["date"])
    return f"{day.month}月{day.day}日 {at}"


def local_text(value: datetime, timezone: str) -> str:
    local = _aware(value).astimezone(ZoneInfo(timezone))
    return f"{local:%Y-%m-%d %H:%M} 周{DAY_NAMES[local.weekday()]}"


def validate_options(db, user, body):
    if body.project_id:
        owned_project(db, body.project_id, user)
    spec = get_catalog().get(body.model or get_catalog().default_id())
    if not spec or not spec.available:
        raise HTTPException(422, "模型不可用")
    if body.skill and (
        resolve_name(body.skill) in run_disabled_skills(db, user)
        or not (skills().get(resolve_name(body.skill)) or usable_personal(db, user).get(body.skill))
    ):
        raise HTTPException(422, "技能不存在或已关闭")


def owned(db, user, task_id):
    task = db.get(ScheduledTask, task_id, populate_existing=True)
    if not task or task.user_id != user.id:
        raise HTTPException(404, "定时任务不存在")
    return task


def output(db, task):
    result = {c.name: getattr(task, c.name) for c in ScheduledTask.__table__.columns}
    run = db.get(Run, task.last_run_id) if task.last_run_id else None
    if run and task.last_status not in {"skipped", "waiting"}:
        result["last_status"] = run.status
    conv = db.get(Conversation, task.conversation_id) if task.conversation_id else None
    if conv is not None:
        # a bound task lives wherever its conversation lives
        result["project_id"], result["conversation_title"] = conv.project_id, conv.title
    return result


def list_rows(db, user_id, conversation_id=None):
    query = select(ScheduledTask).where(ScheduledTask.user_id == user_id)
    if conversation_id is not None:
        query = query.where(ScheduledTask.conversation_id == conversation_id)
    return db.scalars(query.order_by(ScheduledTask.created_at, ScheduledTask.id).limit(LIMIT)).all()


@router.get("", response_model=list[TaskOut])
def list_tasks(conversation_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [output(db, t) for t in list_rows(db, user.id, conversation_id)]


def create_row(db, user, body: TaskInput, *, mode: str, conversation: Conversation | None = None) -> ScheduledTask:
    """Caller holds the user lock and commits."""
    if db.scalar(select(func.count()).select_from(ScheduledTask).where(ScheduledTask.user_id == user.id)) >= LIMIT:
        raise HTTPException(422, f"最多 {LIMIT} 个定时任务")
    validate_options(db, user, body)
    validate_end(body.ends_on, body.timezone)
    values = body.model_dump()
    if conversation is not None:
        values["project_id"] = conversation.project_id
    task = ScheduledTask(
        user_id=user.id,
        **values,
        mode=mode,
        conversation_id=conversation.id if conversation is not None else None,
        next_run_at=first_occurrence(values["schedule"], body.timezone, utcnow()),
    )
    db.add(task)
    db.flush()
    record_audit(db, "schedule.created", "schedule", task.id, actor_id=user.id)
    return task


@router.post("", response_model=TaskOut, status_code=201)
def create_task(body: TaskInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        # made on the schedules page: its own Work conversation starts with the first occurrence
        task = create_row(db, user, body, mode="work")
        db.commit()
        return output(db, task)


def update_row(db, user, task, changes: dict, fields: set[str]):
    """Caller holds the user lock and commits."""
    values = {k: getattr(task, k) for k in TaskInput.model_fields}
    values.update(changes)
    try:
        parsed = TaskInput.model_validate(values)
    except ValidationError as exc:
        message = next((str(e.get("ctx", {}).get("error") or "") for e in exc.errors() if e.get("ctx", {}).get("error")), "")
        raise HTTPException(422, message or "定时任务参数无效") from None
    if task.conversation_id and "project_id" in fields and parsed.project_id != task.project_id:
        raise HTTPException(422, "定时任务跟随会话所在项目")
    validate_options(db, user, parsed)
    if {"ends_on", "timezone"} & fields:
        validate_end(parsed.ends_on, parsed.timezone)
    timing = {"schedule", "timezone", "ends_on"} & fields
    upcoming = first_occurrence(parsed.schedule.model_dump(), parsed.timezone, utcnow()) if timing or parsed.enabled else None
    for key, value in parsed.model_dump().items():
        setattr(task, key, value)
    if timing or (parsed.enabled and "enabled" in fields):
        task.paused_reason, task.failure_streak, task.recent_run_ids = None, 0, []
    if timing or "enabled" in fields:
        task.next_run_at, task.due_at = upcoming or task.next_run_at, None
    if task.enabled and task.conversation_id:
        conv = db.get(Conversation, task.conversation_id)
        if conv is None or conv.deleted_at is not None:
            raise HTTPException(422, "会话已删除")
    task.updated_at = utcnow()
    record_audit(db, "schedule.updated", "schedule", task.id, actor_id=user.id)


@router.patch("/{task_id}", response_model=TaskOut)
def patch_task(task_id: str, body: TaskPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        task = owned(db, user, task_id)
        update_row(db, user, task, body.model_dump(exclude_unset=True), body.model_fields_set)
        db.commit()
        return output(db, task)


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        task = owned(db, user, task_id)
        record_audit(db, "schedule.deleted", "schedule", task.id, actor_id=user.id)
        db.delete(task)
        db.commit()
    return Response(status_code=204)


def drop_for_conversations(db, user_id, conversation_ids):
    """A task has nowhere to reply once its conversation is deleted; it goes in the same transaction."""
    ids = list(conversation_ids)
    if not ids:
        return
    for task in db.scalars(select(ScheduledTask).where(ScheduledTask.user_id == user_id, ScheduledTask.conversation_id.in_(ids))):
        record_audit(db, "schedule.deleted", "schedule", task.id, actor_id=user_id)
        db.delete(task)


def validate_end(ends_on, timezone):
    if ends_on is not None and ends_on < utcnow().astimezone(ZoneInfo(timezone)).date():
        raise HTTPException(422, "截止日期不能早于今天")


def pause_task(db, task, reason):
    if task.paused_reason is not None and not task.enabled:
        return
    from .inbox import create_notification

    task.enabled, task.paused_reason, task.due_at = False, reason, None
    task.updated_at = utcnow()
    if reason != "done":
        create_notification(db, task.user_id, "schedule.paused", task.title, task.conversation_id)


def record_failure(db, task):
    task.failure_streak += 1
    if task.failure_streak >= FAILURE_LIMIT:
        pause_task(db, task, "failures")


def _aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def unattended(db, task, now):
    ids = task.recent_run_ids or []
    if len(ids) < UNREAD_OCCURRENCES:
        return False
    oldest = None
    for rid in ids:
        run = db.get(Run, rid)
        if not run or run.status == RunStatus.CANCELED:
            return False
        conv = db.get(Conversation, run.conversation_id) if run.conversation_id else None
        if not conv or conv.deleted_at:
            return False
        notes = db.scalars(
            select(Notification).where(Notification.run_id == rid, Notification.kind.in_(["schedule.succeeded", "schedule.failed"]))
        ).all()
        if not notes or any(n.read_at for n in notes):
            return False
        started = _aware(run.started_at or run.created_at)
        oldest = min(oldest, started) if oldest else started
    # the user answering in the conversation since then is attention too
    if task.conversation_id and db.scalar(
        select(Run.id)
        .where(Run.conversation_id == task.conversation_id, Run.id.not_in(ids), Run.created_at > oldest, ~own_turn(task.id))
        .limit(1)
    ):
        return False
    return now - oldest >= UNREAD_GRACE


def own_turn(task_id):
    return func.coalesce(Run.options["schedule_id"].as_string(), "") == task_id


def busy_run(db, conversation_id):
    return db.scalar(
        select(Run)
        .where(Run.conversation_id == conversation_id, Run.status.not_in(RunStatus.TERMINAL))
        .order_by(Run.created_at.desc())
        .limit(1)
    )


def dispatch_task(db, task, user, now, occurrence, regular):
    """`occurrence` is the wall-clock time this turn stands for; `regular` the next one after it (None: a single task)."""
    zone = ZoneInfo(task.timezone)
    if task.ends_on and max(occurrence.astimezone(zone).date(), now.astimezone(zone).date()) > task.ends_on:
        pause_task(db, task, "ended")
        return
    last = db.get(Run, task.last_run_id) if task.last_run_id else None
    stalled = last and last.status in {RunStatus.WAITING_USER, RunStatus.INTERRUPTED}
    if stalled and now - _aware(last.started_at or last.created_at) >= UNREAD_GRACE:
        # a question nobody answers would otherwise skip every later occurrence without end
        pause_task(db, task, "unread")
        return
    conv = db.get(Conversation, task.conversation_id) if task.conversation_id else None
    blocking = busy_run(db, conv.id) if conv is not None else None
    if blocking is not None:
        waiting = blocking.status in {RunStatus.WAITING_USER, RunStatus.INTERRUPTED}
        retry = now + DEFER_RETRY
        limit = min(occurrence + DEFER_LIMIT, regular) if regular else occurrence + DEFER_LIMIT
        if not waiting and retry < limit:
            # someone is mid-turn in this conversation: reply right after it instead of interrupting
            task.due_at, task.next_run_at = occurrence, retry
            task.last_status, task.last_error = "waiting", None
            task.updated_at = now
            return
        task.due_at = None
        task.last_status = "skipped"
        task.last_error = "会话在等你回复" if waiting else "会话一直在回答中"
        task.updated_at = now
    elif conv is None and last and last.status not in RunStatus.TERMINAL:
        # an older task without a conversation yet: never two of its turns at once
        task.last_status, task.last_error = "skipped", "上一次还未结束"
        task.updated_at = now
    elif unattended(db, task, now):
        pause_task(db, task, "unread")
        return
    else:
        task.due_at = None
        execute_task(db, task, user, now, occurrence, dispatched=True)
    if regular is None and task.due_at is None:
        if task.last_status == "failed":
            pause_task(db, task, "failures")
        else:
            # a single task that ran is finished: its answer stays in the conversation, its slot is free again
            record_audit(db, "schedule.deleted", "schedule", task.id, actor_id=task.user_id)
            db.delete(task)
    elif regular is not None and task.ends_on and _aware(task.next_run_at).astimezone(zone).date() > task.ends_on:
        pause_task(db, task, "ended")


def execute_task(db, task, user, now, occurrence, *, dispatched=False, manual=False):
    task.last_run_at, task.last_run_id = now, None
    try:
        with db.begin_nested():
            db.refresh(user)
            if user.disabled_at:
                raise HTTPException(403, "账号已停用")
            conv = db.get(Conversation, task.conversation_id, populate_existing=True) if task.conversation_id else None
            if task.conversation_id and (conv is None or conv.deleted_at is not None):
                raise HTTPException(410, "会话已删除")
            leaf = db.get(Run, conv.active_run_id) if conv is not None and conv.active_run_id else None
            if conv is None:
                conv = Conversation(user_id=user.id, project_id=task.project_id, title=task.title)
                db.add(conv)
                db.flush()
            model = (conv.model if leaf is not None else None) or task.model
            reasoning = (leaf.options or {}).get("reasoning") if leaf is not None and leaf.model == model else None
            run = start_run(
                db,
                user,
                task.prompt,
                model,
                [],
                title=task.title,
                conversation=conv,
                # an existing conversation keeps its own mode; a new one starts in the task's
                mode=None if leaf is not None else task.mode,
                skill=task.skill,
                reasoning=reasoning,
                # the conversation's own surface: the client decides the tool list, and a turn that changes it
                # breaks the conversation's cached prefix (ADR 0030). Tasks are made in the Portal.
                client=(leaf.options or {}).get("client") if leaf is not None else "portal",
                locked=True,
                commit=False,
            )
            run.options = {
                **run.options,
                "schedule_id": task.id,
                "schedule": {
                    "title": task.title,
                    "rule": rule_text(task.schedule),
                    "at": local_text(occurrence, task.timezone),
                    **({"manual": True} if manual else {}),
                },
            }
            task.conversation_id = conv.id
            task.mode = run.options.get("mode", task.mode)
        task.recent_run_ids = [*(task.recent_run_ids or []), run.id][-UNREAD_OCCURRENCES:]
        task.last_run_id, task.last_conversation_id = run.id, conv.id
        task.last_status, task.last_error = run.status, None
        EXECUTIONS.labels("started").inc()
    except HTTPException as exc:
        task.last_status = "failed"
        task.last_error = START_ERRORS.get(exc.status_code, "任务无法启动，请检查模型、技能及额度设置")
        if dispatched:
            record_failure(db, task)
        EXECUTIONS.labels("failed").inc()
    task.updated_at = now


@router.post("/{task_id}/run", status_code=201)
def run_now(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        task = owned(db, user, task_id)
        if task.conversation_id and busy_run(db, task.conversation_id) is not None:
            raise HTTPException(409, "会话正在回答中，稍后再试")
        now = utcnow()
        execute_task(db, task, user, now, now, manual=True)
        db.commit()
        if task.last_status == "failed":
            raise HTTPException(422, task.last_error)
        return {"conversation_id": task.last_conversation_id, "run_id": task.last_run_id}


def dispatch_schedules(now=None, session_factory=SessionLocal):
    now = now or utcnow()
    deadline = time.monotonic() + settings.schedules_deadline_seconds
    count = 0
    with session_factory() as db:
        candidates = list(
            db.execute(
                select(ScheduledTask.id, ScheduledTask.user_id, ScheduledTask.next_run_at)
                .where(ScheduledTask.enabled.is_(True), ScheduledTask.next_run_at <= now)
                .order_by(ScheduledTask.next_run_at)
                .limit(100)
            )
        )
        db.rollback()
        for task_id, user_id, observed in candidates:
            if time.monotonic() >= deadline:
                break
            with user_critical_section(db, user_id):
                task = db.get(ScheduledTask, task_id, populate_existing=True)
                if not task:
                    db.rollback()
                    continue
                occurrence = _aware(task.due_at or observed).astimezone(UTC)
                regular = next_occurrence(task.schedule, task.timezone, max(now, occurrence))
                claimed = db.execute(
                    update(ScheduledTask)
                    .where(ScheduledTask.id == task_id, ScheduledTask.enabled.is_(True), ScheduledTask.next_run_at == observed)
                    # a single task keeps its time: it is disabled below, never claimed twice
                    .values(next_run_at=regular or observed)
                    .execution_options(synchronize_session=False)
                ).rowcount
                if claimed:
                    db.refresh(task)
                    dispatch_task(db, task, db.get(User, user_id), now, occurrence, regular)
                    count += 1
                db.commit()
    return count


async def schedules_forever():
    if not settings.schedules_enabled or settings.env == "test":
        return
    while True:
        try:
            await asyncio.to_thread(dispatch_schedules)
        except Exception:
            log.exception("schedule dispatch failed")
        await asyncio.sleep(settings.schedules_interval_seconds)


internal_router = APIRouter(tags=["internal"])


class ScheduleOperation(BaseModel):
    action: Literal["list", "create", "update", "delete"]
    id: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, max_length=60)
    prompt: str | None = None
    schedule: dict | None = None
    ends_on: date | None = None
    enabled: bool | None = None


def user_timezone(db, user) -> str:
    """The zone the user last scheduled in, else the one memory consolidation uses, else the platform default."""
    latest = db.scalar(
        select(ScheduledTask.timezone).where(ScheduledTask.user_id == user.id).order_by(ScheduledTask.updated_at.desc()).limit(1)
    )
    from ..memory_dream.settings import dream_settings

    return latest or dream_settings(user).timezone or settings.memory_dream_default_timezone


def soonest_date(schedule: dict | None, zone: str) -> dict | None:
    """A single time without a date means the next time the clock shows it."""
    if not isinstance(schedule, dict) or schedule.get("kind") != "once" or schedule.get("date"):
        return schedule
    try:
        hour, minute = map(int, str(schedule.get("time")).split(":"))
        local = utcnow().astimezone(ZoneInfo(zone))
        day = local.date() + timedelta(days=1 if (hour, minute) <= (local.hour, local.minute) else 0)
    except ValueError:
        return schedule
    return {**schedule, "date": day.isoformat()}


def model_view(task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "prompt": task.prompt,
        "when": rule_text(task.schedule),
        "next": local_text(task.next_run_at, task.timezone) if task.enabled else None,
        "enabled": task.enabled,
        **({"ends_on": task.ends_on.isoformat()} if task.ends_on else {}),
        **({"paused": task.paused_reason} if task.paused_reason and not task.enabled else {}),
    }


@internal_router.post("/internal/runs/{run_id}/schedules", dependencies=[Depends(require_scope("schedules:write_for_run"))])
def run_schedules(
    run_id: str,
    body: ScheduleOperation,
    x_run_attempt: str | None = Header(default=None),
    x_delivery_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """The model's schedule tool: tasks of the conversation this turn belongs to, and only those."""
    from .routes_internal import _run_or_404
    from .runtime_fence import fence_attempt

    run = _run_or_404(db, run_id)
    with user_critical_section(db, run.user_id):
        db.refresh(run)
        fence_attempt(db, run.id, x_run_attempt)
        if run.status != RunStatus.RUNNING:
            raise HTTPException(409, "run is not running")
        user = db.get(User, run.user_id, populate_existing=True)
        conv = db.get(Conversation, run.conversation_id) if run.conversation_id else None
        if conv is None or conv.deleted_at is not None:
            return {"ok": False, "error": "只能在会话中设置定时任务"}
        zone = user_timezone(db, user)
        now = {"now": local_text(utcnow(), zone), "timezone": zone}
        if body.action == "list":
            rows = list_rows(db, user.id, conv.id)
            total = db.scalar(select(func.count()).select_from(ScheduledTask).where(ScheduledTask.user_id == user.id))
            return {"ok": True, **now, "tasks": [model_view(t) for t in rows], "slots_left": max(0, LIMIT - total)}
        if not x_delivery_id or not x_delivery_id.startswith(run.id + ":") or len(x_delivery_id) > 160:
            raise HTTPException(422, "stable delivery identity required")
        if prior := db.get(RunDelivery, (run.id, x_delivery_id)):
            return prior.result
        try:
            if body.action == "create":
                if (run.options or {}).get("schedule_id"):
                    raise HTTPException(422, "定时触发的回答不能再新建定时任务")
                parsed = TaskInput.model_validate(
                    {
                        "title": body.title or "",
                        "prompt": body.prompt or "",
                        "schedule": soonest_date(body.schedule, zone) or {},
                        "timezone": zone,
                        "ends_on": body.ends_on,
                        "enabled": True,
                    }
                )
                task = create_row(db, user, parsed, mode=(run.options or {}).get("mode", "work"), conversation=conv)
                result = {"ok": True, **now, "created": model_view(task)}
            else:
                task = db.get(ScheduledTask, body.id or "", populate_existing=True)
                if task is None or task.user_id != user.id or task.conversation_id != conv.id:
                    raise HTTPException(422, "本会话没有这个定时任务")
                if body.action == "delete":
                    result = {"ok": True, **now, "deleted": model_view(task)}
                    record_audit(db, "schedule.deleted", "schedule", task.id, actor_id=user.id)
                    db.delete(task)
                else:
                    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k not in {"action", "id"}}
                    if "schedule" in changes:
                        changes["schedule"] = soonest_date(changes["schedule"], task.timezone)
                    if not changes:
                        raise HTTPException(422, "没有要修改的内容")
                    update_row(db, user, task, changes, set(changes))
                    result = {"ok": True, **now, "updated": model_view(task)}
        except ValidationError as exc:
            db.rollback()
            message = next((str(e.get("ctx", {}).get("error") or "") for e in exc.errors() if e.get("ctx", {}).get("error")), "")
            return {"ok": False, **now, "error": message or "参数无效：需要 title、prompt 和 schedule"}
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            db.rollback()
            error = exc.detail + ("；用户可在「定时任务」页删除不用的" if str(exc.detail).startswith("最多") else "")
            return {"ok": False, **now, "error": error}
        db.add(RunDelivery(run_id=run.id, delivery_id=x_delivery_id, result=result))
        db.commit()
        return result
