"""Run lifecycle, SSE events and cancellation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import metrics
from ..accounts import LoginThrottle
from ..agent.prompts import skills
from ..catalog import get_catalog
from ..config import settings
from ..db import SessionLocal
from ..deps import current_user, current_user_short, get_db
from ..events import append_event, list_events
from ..library import owned_project
from ..locks import user_critical_section
from ..models import Conversation, FileObject, FileStatus, OutboxMessage, QueuedMessage, Run, RunAttempt, RunEvent, RunStatus, User, utcnow
from ..quota import enforce_token_quota, usage_summary, user_limits
from ..run_state import transition_run
from ..schemas import EventOut, RunCreate, RunDetail, RunEdit, RunOut, RunRetry
from ..skill_policy import effective_policy
from ..skill_registry import BASE_SKILLS, resolve_name
from ..storage import get_storage
from .conversation_mode import resolve_conversation_mode
from .routes_skills import personal_skills, usable_personal
from .serializers import run_dict
from .ui_state import UIState

router = APIRouter(prefix="/v1", tags=["runs"])
log = logging.getLogger("ailab.api.runs")
ui_state_throttle = LoginThrottle(120, 60)


def _get_owned_run(db: Session, run_id: str, user: User) -> Run:
    run = db.get(Run, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return run


def _owns_run(run_id: str, user_id: str) -> bool:
    """Ownership check on a throwaway session, safe to call from a worker thread."""
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        return run is not None and run.user_id == user_id


@router.get("/usage")
def my_usage(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return usage_summary(db, user.id)


@router.get("/models")
def list_models(_: User = Depends(current_user)) -> dict:
    """The models this user can choose from. Never exposes upstream URLs, upstream names or keys."""
    catalog = get_catalog()
    default = catalog.default_id()
    from ..model_policy import ModelPolicy

    return {
        "default": default,
        "models": [
            {
                **m.public(),
                "default": m.id == default,
                "context_tokens": m.context_tokens,
                "input_budget_tokens": ModelPolicy.input_budget(m),
            }
            for m in catalog.available()
        ],
    }


def reject_both_skill_fields(body):
    if {"skill", "skills"} <= body.model_fields_set:
        raise HTTPException(400, "不能同时指定 skill 和 skills")


def validate_run_input(
    db, user, prompt, model, file_ids, mode=None, skill=None, template=None, *, selections=None, conversation=None, turn_mode=None
):
    if len(prompt) > settings.max_prompt_chars:
        raise HTTPException(status_code=413, detail=f"prompt exceeds {settings.max_prompt_chars} chars")
    options: dict = {"mode": mode, "capability_discovery": 1}
    if skill is not None and selections is not None:
        raise HTTPException(400, "不能同时指定 skill 和 skills")
    requested = selections if selections is not None else ([{"name": skill}] if skill else [])
    if len(requested) > 5:
        raise HTTPException(400, "最多选择5个技能")
    personal = personal_skills(db, user)
    allowed = usable_personal(db, user)
    off = set((user.settings or {}).get("disabled_skills") or [])
    denied, personal_policy = effective_policy(db, user)
    resolved, seen = [], set()
    for raw in requested:
        item = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        name = resolve_name(item["name"])
        position = item.get("at")
        if position is not None and (not isinstance(position, int) or position < 0 or position > len(prompt)):
            raise HTTPException(400, f"技能「{item['name']}」的位置无效")
        candidate = None
        if item.get("file_id"):
            source = db.get(FileObject, item["file_id"])
            if source and source.user_id == user.id and source.purpose == "skill":
                candidate = next((s for s in personal if s.get("lineage_id") == source.lineage_id), None)
            if candidate is None:
                raise HTTPException(400, f"技能「{item['name']}」不存在或已删除")
            name = candidate["name"]
        else:
            candidate = next((s for s in personal if s["name"] == name), None)
        platform = skills().get(name)
        known = platform or candidate
        title = (known or {}).get("title") or item["name"]
        if not known:
            raise HTTPException(400, f"技能「{title}」不存在或已删除")
        if name in off and name not in BASE_SKILLS:
            raise HTTPException(400, f"技能「{title}」已关闭")
        if name in denied or (candidate and not platform and personal_policy == "disabled"):
            raise HTTPException(400, f"技能「{title}」已被管理员停用")
        if candidate and not platform and name not in allowed:
            error = candidate.get("error") or "未通过审核"
            if error == "安全扫描中":
                error = "还在安全扫描"
            raise HTTPException(400, f"技能「{title}」{error}")
        identity = ("personal", candidate["file_id"]) if candidate and not platform else ("platform", name)
        if identity in seen:
            continue
        seen.add(identity)
        frozen = {
            "name": name,
            "title": title,
            "kind": "base" if name in BASE_SKILLS else "builtin" if platform else "personal",
            "at": position,
        }
        if candidate and not platform:
            frozen["file_id"] = candidate["file_id"]
        resolved.append(frozen)
    options["skills"] = resolved

    mode = resolve_conversation_mode(
        db, conversation, mode, turn_mode=turn_mode, requires_work=bool(template is not None and conversation is not None)
    )
    options["mode"] = mode

    catalog = get_catalog()
    model_id = model or catalog.default_id()
    spec = catalog.get(model_id)
    if spec is None or not spec.available:
        raise HTTPException(status_code=400, detail=f"model not available: {model_id}")

    inputs: list[str] = []
    image_count = 0
    for file_id in file_ids:
        file = db.get(FileObject, file_id, populate_existing=True)
        if (
            file is None
            or file.user_id != user.id
            or file.purpose != "library"
            or file.deleted_at is not None
            or file.purged_at is not None
        ):
            raise HTTPException(status_code=400, detail=f"unknown file_id: {file_id}")
        if file.status != FileStatus.READY:
            raise HTTPException(
                status_code=409,
                detail={"code": "file_not_ready", "message": f"文件还没检查完（{file.status}），稍候再发送"},
            )
        if (file.content_type or "").startswith("image/"):
            image_count += 1
            if image_count > 5:
                raise HTTPException(422, {"code": "attachment_image_limit", "message": "每轮最多附加5张图片"})
        if file.id not in inputs:
            inputs.append(file.id)
    if mode == "work" and inputs and not spec.supports({"tools"}):
        raise HTTPException(422, {"code": "work_model_requires_tools", "message": "请选择支持工具的模型，或切换对话模式读取文件"})
    if mode == "work" and inputs and not spec.supports({"vision"}):
        images = [fid for fid in inputs if (db.get(FileObject, fid).content_type or "").startswith("image/")]
        if images:
            from .attachment_input import validate_chat_files

            validate_chat_files(db, images, model_id, "")
    if mode == "chat" and inputs:
        from .attachment_input import validate_chat_files

        validate_chat_files(db, inputs, model_id, prompt)
    from .templates import freeze_template

    if template is not None:
        options["template"] = freeze_template(db, user, template, mode)
    enforce_token_quota(db, user.id)
    return model_id, options, inputs


def start_run(
    db: Session,
    user: User,
    prompt: str,
    model: str | None,
    file_ids: list[str],
    title: str | None = None,
    conversation: Conversation | None = None,
    mode: str | None = None,
    skill: str | None = None,
    selections=None,
    locked: bool = False,
    commit: bool = True,
    sibling_of: Run | None = None,
    template=None,
    frozen_template: dict | None = None,
    client_send_id: str | None = None,
) -> Run:
    """Check the limits, write the run and queue it. Used for standalone runs and conversation turns."""
    expected_active = conversation.active_run_id if conversation else None
    with nullcontext() if locked else user_critical_section(db, user.id):
        send_digest = None
        if client_send_id is not None:
            # Both send endpoints share the user lock; a queued identity cannot become a second direct call.
            if db.scalar(select(QueuedMessage.id).where(QueuedMessage.user_id == user.id, QueuedMessage.client_send_id == client_send_id)):
                raise HTTPException(409, "client_send_id already used for a queue send")
            send_digest = hashlib.sha256(
                json.dumps(
                    {
                        "prompt": prompt,
                        "model": model,
                        "file_ids": file_ids,
                        "title": title,
                        "conversation_id": conversation.id if conversation else None,
                        "mode": mode,
                        "skill": skill,
                        "skills": [s.model_dump() if hasattr(s, "model_dump") else s for s in selections]
                        if selections is not None
                        else None,
                        "template": template.model_dump(mode="json") if template is not None else None,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            prior = db.scalar(select(Run).where(Run.user_id == user.id, Run.client_send_id == client_send_id))
            if prior is not None:
                if (prior.options or {}).get("client_send_digest") != send_digest:
                    raise HTTPException(409, "client_send_id already used for a different message")
                # Dedupe precedes readiness, quota and active-turn checks: a lost response must be retriable.
                return prior
        model_id, options, inputs = validate_run_input(
            db,
            user,
            prompt,
            model,
            file_ids,
            mode,
            skill,
            template,
            selections=selections,
            conversation=conversation,
            turn_mode=(sibling_of.options or {}).get("mode", "work") if sibling_of else None,
        )
        if send_digest is not None:
            options["client_send_digest"] = send_digest
        if frozen_template:
            if options["mode"] != "work":
                raise HTTPException(400, "仅工作模式可使用模板")
            options["template"] = dict(frozen_template)

        active = db.scalar(select(func.count()).select_from(Run).where(Run.user_id == user.id, Run.status.in_(RunStatus.ACTIVE)))
        if active >= user_limits(db, user.id).max_concurrent_runs:
            metrics.QUOTA_REJECTIONS.labels("active_runs").inc()
            raise HTTPException(status_code=429, detail="too many active runs for this user")
        if conversation is not None:
            db.refresh(conversation)  # project deletion may have completed while this request waited
            if conversation.active_run_id != expected_active:
                raise HTTPException(409, "对话路径已改变，请刷新后重试")
            from ..conversation_tree import path_ids, require_idle

            require_idle(db, conversation.id)
            if not sibling_of and len(path_ids(db, conversation.id, conversation.active_run_id)) >= 500:
                raise HTTPException(422, "对话路径最多500轮")
            if conversation.deleted_at is not None:
                raise HTTPException(status_code=404, detail="conversation not found")
            if conversation.project_id is not None:
                owned_project(db, conversation.project_id, user).updated_at = utcnow()

        if conversation is not None and conversation.memory_policy:
            options = {**options, "memory_policy": dict(conversation.memory_policy or {})}
        run = Run(
            user_id=user.id,
            client_send_id=client_send_id,
            context_source_epoch=db.scalar(select(User.context_source_epoch).where(User.id == user.id)),
            conversation_id=conversation.id if conversation is not None else None,
            parent_run_id=sibling_of.parent_run_id if sibling_of else expected_active,
            title=(title or prompt.strip().splitlines()[0])[:200],
            prompt=prompt,
            model=model_id,
            input_file_ids=inputs,
            options=options,
        )
        db.add(run)
        if conversation is not None:
            conversation.model = model_id
            conversation.updated_at = utcnow()
        db.flush()
        from .document_sources import bind_refs

        bind_refs(db, "run_prompt", run.id, inputs, run.id)
        from ..models import AppliedMessagePosition

        db.add(AppliedMessagePosition(run_id=run.id, message_kind="run_prompt", message_id=run.id, application_seq=0))
        if conversation is not None:
            changed = db.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id, Conversation.active_run_id == expected_active)
                .values(active_run_id=run.id)
            ).rowcount
            if not changed:
                raise HTTPException(409, "对话路径已改变，请刷新后重试")
        from .jobs import stage_job

        stage_job(db, "run", run_id=run.id, mode=options["mode"])
        append_event(db, run.id, "run.created", {"title": run.title, "status": run.status, "model": run.model}, commit=False)
        if commit:
            db.commit()
            db.refresh(run)

    return run


@router.post("/runs", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
def create_run(body: RunCreate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    reject_both_skill_fields(body)
    run = start_run(
        db,
        user,
        body.prompt,
        body.model,
        body.file_ids,
        body.title,
        mode=body.mode,
        skill=body.skill,
        selections=body.skills,
        template=body.template,
        client_send_id=body.client_send_id,
    )
    return run_dict(db, run, detail=True)


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    runs = db.scalars(select(Run).where(Run.user_id == user.id).order_by(Run.created_at.desc()).limit(limit).offset(offset)).all()
    return [run_dict(db, run) for run in runs]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    run = _get_owned_run(db, run_id, user)
    return run_dict(db, run, detail=True)


@router.put("/runs/{run_id}/ui-state", status_code=204)
def put_ui_state(run_id: str, body: UIState, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    run = _get_owned_run(db, run_id, user)
    if run.conversation_id:
        conversation = db.get(Conversation, run.conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise HTTPException(404, "run not found")
    # A sync route (the commit must not block the event loop). The in-memory throttle is per API process; two
    # threads racing past it cost at most an extra write or two, it only exists to stop a broken client.
    if wait := ui_state_throttle.retry_after(user.id):
        raise HTTPException(429, "UI state rate limit", headers={"Retry-After": str(wait)})
    ui_state_throttle.fail(user.id)
    run.ui_state = body.model_dump() if body.blocks else None
    db.commit()
    return Response(status_code=204)


@router.post("/runs/{run_id}/cancel", response_model=RunDetail)
def cancel_run(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    run = _get_owned_run(db, run_id, user)
    with user_critical_section(db, user.id):
        db.refresh(run)
        if run.status in RunStatus.TERMINAL:
            raise HTTPException(status_code=409, detail=f"run already {run.status}")
        run, _ = transition_run(db, run.id, RunStatus.CANCELED, reason="user_requested", commit=False)
        db.commit()
    return run_dict(db, run, detail=True)


@router.get("/runs/{run_id}/events/history", response_model=list[EventOut])
def event_history(
    run_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[RunEvent]:
    run = _get_owned_run(db, run_id, user)
    from ..memory_recall.policy import source_available

    return list_events(db, run_id, after, limit) if source_available(db, run) else []


PREVIEW_NAME = re.compile(r"^[0-9]{1,4}/(p-[0-9]{1,3}\.jpg|sheets\.json|document\.pdf)$")


@router.get("/runs/{run_id}/previews/{name:path}")
def get_preview(run_id: str, name: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    """A rendered page (JPEG) or sheet data (JSON) of a file the run produced. Written by the Broker."""
    run = _get_owned_run(db, run_id, user)
    from ..memory_recall.policy import source_available

    if not source_available(db, run):
        raise HTTPException(404, "preview source unavailable")
    if not PREVIEW_NAME.match(name):
        raise HTTPException(status_code=404, detail="no such preview")
    try:
        data = get_storage().get_bytes(f"previews/{run_id}/{name}")
    except Exception as exc:
        raise HTTPException(status_code=404, detail="no such preview") from exc
    media = "image/jpeg" if name.endswith(".jpg") else "application/pdf" if name.endswith(".pdf") else "application/json"
    # immutable: every render gets a new number
    return Response(content=data, media_type=media, headers={"Cache-Control": "private, max-age=86400, immutable"})


SSE_FAST_POLLS = 8  # ~2 s of quiet at the fast rate before backing off


def sse_poll_delay(idle_polls: int) -> float:
    """Seconds until the next poll of a run's events.

    Fast (0.25 s) while events flow and for ~2 s after the last one: a streamed answer arrives in batches about
    0.25 s apart, so a single empty poll in between must not drop the stream to the slow rate and make it
    stutter. After that, 1 s: 200 idle viewers at 4 polls/s would be 800 DB queries/s. Jitter spreads them out.
    """
    base = 0.25 if idle_polls < SSE_FAST_POLLS else 1.0
    return base + random.uniform(0, 0.05 if base < 1 else 0.15)


def _poll_events(run_id: str, after: int) -> tuple[list[tuple[int, str, dict]], str, bool]:
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        status = run.status if run else RunStatus.FAILED
        # Automatic file waits keep SSE alive across suspension/continuation.
        pending_title = bool(run and status == RunStatus.INTERRUPTED and (run.runtime_meta or {}).get("file_dependency"))
        if run and status in {RunStatus.SUCCEEDED, RunStatus.FAILED} and run.finished_at:
            finished = run.finished_at.replace(tzinfo=UTC) if run.finished_at.tzinfo is None else run.finished_at
            if datetime.now(UTC) < finished + timedelta(seconds=10):
                pending_title = bool(
                    db.scalar(
                        select(Conversation.id)
                        .join(RunAttempt, RunAttempt.run_id == run.id)
                        .join(OutboxMessage, OutboxMessage.claim_token == RunAttempt.id)
                        .where(
                            Conversation.id == run.conversation_id,
                            Conversation.title == "",
                            Conversation.deleted_at.is_(None),
                            RunAttempt.ended_at.is_(None),
                            OutboxMessage.claimed_until > datetime.now(UTC),
                        )
                        .limit(1)
                    )
                )
        # Read events last: a title that cleared the pending check committed its event
        # in the same transaction, so it must be drained before stream.end.
        from ..memory_recall.policy import source_available

        if run and not source_available(db, run):
            return [], status, False
        rows = db.scalars(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after).order_by(RunEvent.seq).limit(200)).all()
        return [(e.seq, e.type, e.payload or {}) for e in rows], status, pending_title


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    user: User = Depends(current_user_short),
) -> StreamingResponse:
    # Do not keep a Session (or its pool connection) open for the stream: the
    # dependency above closes its session immediately. The ownership check is a
    # short query, always run off the event loop.
    if not await asyncio.to_thread(_owns_run, run_id, user.id):
        raise HTTPException(status_code=404, detail="run not found")

    last_id = after
    if not last_id:
        header = request.headers.get("last-event-id")
        if header and header.isdigit():
            last_id = int(header)

    async def gen():
        nonlocal last_id
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            events, current_status, pending_title = await asyncio.to_thread(_poll_events, run_id, last_id)
            for seq, type_, payload in events:
                last_id = seq
                data = json.dumps(payload, ensure_ascii=False)
                yield f"id: {seq}\nevent: {type_}\ndata: {data}\n\n"
            if current_status in RunStatus.TERMINAL | {RunStatus.WAITING_USER, RunStatus.INTERRUPTED} and not events and not pending_title:
                yield "event: stream.end\ndata: {}\n\n"
                return
            if events:
                idle = 0
            else:
                idle += 1
                if idle % 20 == 0:
                    yield ": ping\n\n"
            await asyncio.sleep(sse_poll_delay(idle))

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _new_version(db, user, run_id, body, editing=False):
    from ..conversation_tree import path_ids, require_idle, siblings_query

    if editing:
        reject_both_skill_fields(body)
    with user_critical_section(db, user.id):
        old = _get_owned_run(db, run_id, user)
        db.refresh(old)
        conv = db.get(Conversation, old.conversation_id, populate_existing=True) if old.conversation_id else None
        if not conv or conv.deleted_at is not None:
            raise HTTPException(409, "只能修改对话中的消息")
        require_idle(db, conv.id)
        ids = path_ids(db, conv.id, conv.active_run_id)
        if old.status not in RunStatus.TERMINAL or old.id not in ids or (not editing and conv.active_run_id != old.id):
            raise HTTPException(409, "只能编辑当前路径已结束的消息，重试仅限最后一轮")
        if len(list(db.scalars(siblings_query(conv.id, old.parent_run_id).limit(50)))) >= 50:
            raise HTTPException(422, "每轮最多50个版本")
        file_ids = body.file_ids if editing and body.file_ids is not None else old.input_file_ids or []
        for file_id in file_ids:
            file = db.get(FileObject, file_id, populate_existing=True)
            if (
                not file
                or file.user_id != user.id
                or file.status != FileStatus.READY
                or file.deleted_at
                or file.purged_at
                or file.purpose != "library"
            ):
                raise HTTPException(422, "附件已不可用")
        options = dict(old.options or {})
        new = start_run(
            db,
            user,
            body.prompt if editing else old.prompt,
            body.model or old.model,
            file_ids,
            None if editing else old.title,
            conversation=conv,
            mode=body.mode or options.get("mode") or "work",
            skill=(
                body.skill
                if editing and body.skill is not None
                else options.get("skill")
                if options.get("skills") is None and not (editing and body.skills is not None)
                else None
            ),
            selections=(body.skills if editing and body.skills is not None else options.get("skills")),
            locked=True,
            commit=False,
            sibling_of=old,
            frozen_template=options.get("template"),
        )
        new.options = {**{k: v for k, v in options.items() if k not in {"skill", "skill_title", "skills"}}, **new.options}
        conv.queue_paused = True
        if editing:
            new.edited_from = old.id
        else:
            new.retry_of = old.id
            old.superseded_by_run_id = new.id  # legacy audit field, no longer controls path membership
        # The shared summary cache is reusable only at an ancestor of the replacement's parent.
        if conv.summary_through_run_id not in path_ids(db, conv.id, old.parent_run_id):
            conv.summary, conv.summary_through_run_id = None, None
        db.commit()
        return run_dict(db, new, detail=True)


@router.post("/runs/{run_id}/retry", response_model=RunDetail, status_code=201)
def retry_run(run_id: str, body: RunRetry, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return _new_version(db, user, run_id, body)


@router.post("/runs/{run_id}/edit", response_model=RunDetail, status_code=201)
def edit_run(run_id: str, body: RunEdit, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return _new_version(db, user, run_id, body, editing=True)
