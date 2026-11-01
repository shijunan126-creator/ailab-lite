"""Bounded user schedules; occurrences and run outbox share one transaction."""

import asyncio
import logging
import time
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Response
from prometheus_client import Counter
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..catalog import get_catalog
from ..config import settings
from ..db import SessionLocal
from ..deps import current_user, get_db
from ..library import owned_project
from ..locks import user_critical_section
from ..models import Conversation, Notification, Run, RunStatus, ScheduledTask, User, utcnow
from ..schemas import ORMModel
from ..skill_policy import run_disabled_skills
from ..skill_registry import resolve_name
from .routes_runs import skills, start_run, usable_personal

router = APIRouter(prefix="/v1/schedules", tags=["schedules"])
EXECUTIONS = Counter("ailab_schedule_executions_total", "Schedule executions", ["status"])
log = logging.getLogger(__name__)
UNREAD_OCCURRENCES = 3
UNREAD_GRACE = timedelta(days=7)
FAILURE_LIMIT = 3


class Schedule(BaseModel):
    kind: Literal["daily", "weekdays", "weekly"]
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    days: list[int] | None = None

    @model_validator(mode="after")
    def valid_days(self):
        if self.kind == "weekly":
            if not self.days or any(d < 0 or d > 6 for d in self.days) or len(set(self.days)) != len(self.days):
                raise ValueError("请选择有效星期")
        elif self.days is not None:
            raise ValueError("只有每周任务可指定星期")
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
    mode: Literal["chat", "work"] = "chat"
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
        return self


class TaskPatch(NoTemplate):
    title: str | None = None
    prompt: str | None = None
    mode: Literal["chat", "work"] | None = None
    skill: str | None = None
    model: str | None = None
    project_id: str | None = None
    schedule: Schedule | None = None
    timezone: str | None = None
    enabled: bool | None = None
    ends_on: date | None = None


class TaskOut(TaskInput, ORMModel):
    paused_reason: str | None = None
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


def next_occurrence(schedule: dict, timezone: str, after: datetime) -> datetime:
    """Nonexistent wall times skip that day; overlaps fire once at the earlier fold."""
    zone = ZoneInfo(timezone)
    after = after.replace(tzinfo=UTC) if after.tzinfo is None else after.astimezone(UTC)
    day = after.astimezone(zone).date()
    hour, minute = map(int, schedule["time"].split(":"))
    for offset in range(16):
        date = day + timedelta(days=offset)
        allowed = schedule["kind"] == "daily" or (
            date.weekday() < 5 if schedule["kind"] == "weekdays" else date.weekday() in schedule["days"]
        )
        if not allowed:
            continue
        local = datetime(date.year, date.month, date.day, hour, minute, tzinfo=zone, fold=0)
        candidate = local.astimezone(UTC)
        if candidate > after and candidate.astimezone(zone).replace(tzinfo=None) == local.replace(tzinfo=None):
            return candidate
    raise ValueError("无法计算下次执行时间")


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
    if run and task.last_status != "skipped":
        result["last_status"] = run.status
    return result


@router.get("", response_model=list[TaskOut])
def list_tasks(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [
        output(db, t)
        for t in db.scalars(select(ScheduledTask).where(ScheduledTask.user_id == user.id).order_by(ScheduledTask.created_at).limit(5))
    ]


@router.post("", response_model=TaskOut, status_code=201)
def create_task(body: TaskInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        if db.scalar(select(func.count()).select_from(ScheduledTask).where(ScheduledTask.user_id == user.id)) >= 5:
            raise HTTPException(422, "最多 5 个定时任务")
        validate_options(db, user, body)
        validate_end(body.ends_on, body.timezone)
        task = ScheduledTask(
            user_id=user.id, **body.model_dump(), next_run_at=next_occurrence(body.schedule.model_dump(), body.timezone, utcnow())
        )
        db.add(task)
        db.flush()
        record_audit(db, "schedule.created", "schedule", task.id, actor_id=user.id)
        db.commit()
        return output(db, task)


@router.patch("/{task_id}", response_model=TaskOut)
def patch_task(task_id: str, body: TaskPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from pydantic import ValidationError

    with user_critical_section(db, user.id):
        task = owned(db, user, task_id)
        values = {k: getattr(task, k) for k in TaskInput.model_fields}
        values.update(body.model_dump(exclude_unset=True))
        try:
            parsed = TaskInput.model_validate(values)
        except ValidationError:
            raise HTTPException(422, "定时任务参数无效") from None
        validate_options(db, user, parsed)
        if {"ends_on", "timezone"} & body.model_fields_set:
            validate_end(parsed.ends_on, parsed.timezone)
        for key, value in parsed.model_dump().items():
            setattr(task, key, value)
        if {"schedule", "timezone", "ends_on"} & body.model_fields_set or body.enabled is True:
            task.paused_reason, task.failure_streak, task.recent_run_ids = None, 0, []
        if {"schedule", "timezone", "enabled", "ends_on"} & body.model_fields_set:
            task.next_run_at = next_occurrence(task.schedule, task.timezone, utcnow())
        task.updated_at = utcnow()
        record_audit(db, "schedule.updated", "schedule", task.id, actor_id=user.id)
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


def validate_end(ends_on, timezone):
    if ends_on is not None and ends_on < utcnow().astimezone(ZoneInfo(timezone)).date():
        raise HTTPException(422, "截止日期不能早于今天")


def pause_task(db, task, reason):
    if task.paused_reason is not None and not task.enabled:
        return
    from .inbox import create_notification

    task.enabled, task.paused_reason = False, reason
    task.updated_at = utcnow()
    create_notification(db, task.user_id, "schedule.paused", task.title)


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
    return now - oldest >= UNREAD_GRACE


def dispatch_task(db, task, user, now, observed):
    due_date = (observed.replace(tzinfo=UTC) if observed.tzinfo is None else observed).astimezone(ZoneInfo(task.timezone)).date()
    if task.ends_on and max(due_date, now.astimezone(ZoneInfo(task.timezone)).date()) > task.ends_on:
        pause_task(db, task, "ended")
        return
    last = db.get(Run, task.last_run_id) if task.last_run_id else None
    stalled = last and last.status in {RunStatus.WAITING_USER, RunStatus.INTERRUPTED}
    if stalled and now - _aware(last.started_at or last.created_at) >= UNREAD_GRACE:
        # a question nobody answers would otherwise skip every later occurrence without end
        pause_task(db, task, "unread")
        return
    if last and last.status not in RunStatus.TERMINAL:
        task.last_status, task.last_error = "skipped", "上一次还未结束"
        task.updated_at = now
    elif unattended(db, task, now):
        pause_task(db, task, "unread")
        return
    else:
        execute_task(db, task, user, now, dispatched=True)
    if (
        task.ends_on
        and (task.next_run_at.replace(tzinfo=UTC) if task.next_run_at.tzinfo is None else task.next_run_at)
        .astimezone(ZoneInfo(task.timezone))
        .date()
        > task.ends_on
    ):
        pause_task(db, task, "ended")


def execute_task(db, task, user, now, *, dispatched=False):
    task.last_run_at, task.last_run_id, task.last_conversation_id = now, None, None
    try:
        with db.begin_nested():
            db.refresh(user)
            if user.disabled_at:
                raise HTTPException(403, "账号已停用")
            local = now.astimezone(ZoneInfo(task.timezone))
            conv = Conversation(user_id=user.id, project_id=task.project_id, title=f"{task.title} · {local.month}月{local.day}日")
            db.add(conv)
            db.flush()
            run = start_run(
                db, user, task.prompt, task.model, [], conversation=conv, mode=task.mode, skill=task.skill, locked=True, commit=False
            )
            run.options = {**run.options, "schedule_id": task.id}
        task.recent_run_ids = [*(task.recent_run_ids or []), run.id][-UNREAD_OCCURRENCES:]
        task.last_run_id, task.last_conversation_id = run.id, conv.id
        task.last_status, task.last_error = run.status, None
        EXECUTIONS.labels("started").inc()
    except HTTPException as exc:
        task.last_status = "failed"
        task.last_error = {403: "账号已停用", 404: "项目已删除", 429: "额度或并发任务已达上限"}.get(
            exc.status_code, "任务无法启动，请检查模型、技能及额度设置"
        )
        if dispatched:
            record_failure(db, task)
        EXECUTIONS.labels("failed").inc()
    task.updated_at = now


@router.post("/{task_id}/run", status_code=201)
def run_now(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        task = owned(db, user, task_id)
        execute_task(db, task, user, utcnow())
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
                future = next_occurrence(task.schedule, task.timezone, now)
                claimed = db.execute(
                    update(ScheduledTask)
                    .where(ScheduledTask.id == task_id, ScheduledTask.enabled.is_(True), ScheduledTask.next_run_at == observed)
                    .values(next_run_at=future)
                    .execution_options(synchronize_session=False)
                ).rowcount
                if claimed:
                    db.refresh(task)
                    dispatch_task(db, task, db.get(User, user_id), now, observed)
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
