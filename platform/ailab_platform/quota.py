"""User admission and call reservations, serialized at the API's user boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import metrics
from .catalog import get_catalog
from .config import settings
from .contracts.usage import ReserveUsageIn, SettleUsageIn, UsageErrorCode, UsageStatus
from .domain.ledger import MILLI, POLICY_VERSION, reserved_amount, snapshot_weight, weighted_amount
from .domain.quota_policy import Limits, limits_for
from .locks import user_critical_section
from .models import FileObject, FileStatus, ModelUsage, Run, RunStatus, UsageReservation, User


def _error(status: int, code: UsageErrorCode, **detail):
    return HTTPException(status_code=status, detail={"code": code, **detail})


def user_limits(db: Session, user_id: str) -> Limits:
    """The user's own policy, else their department's, else the default policy, else settings."""
    user = db.get(User, user_id)
    if user is None:
        return Limits(
            settings.daily_token_limit, settings.monthly_token_limit, settings.library_quota_bytes, settings.max_active_runs_per_user
        )
    return limits_for(user)


def quota_windows(now: datetime | None = None, limits: Limits | None = None) -> dict[str, tuple[datetime, datetime, int]]:
    daily = limits.daily_tokens if limits else settings.daily_token_limit
    monthly = limits.monthly_tokens if limits else settings.monthly_token_limit
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(settings.quota_timezone))
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {
        "daily_tokens": (day.astimezone(UTC), (day + timedelta(days=1)).astimezone(UTC), daily),
        "monthly_tokens": (month.astimezone(UTC), next_month.astimezone(UTC), monthly),
    }


def _used_query(user_id: str, since: datetime, until: datetime | None = None):
    query = select(func.coalesce(func.sum(ModelUsage.weighted_amount), 0)).where(
        ModelUsage.user_id == user_id, ModelUsage.created_at >= since
    )
    if until is not None:
        query = query.where(ModelUsage.created_at < until)
    return query


def _used_milli(db: Session, user_id: str, since: datetime, until: datetime | None = None) -> int:
    return int(db.scalar(_used_query(user_id, since, until)) or 0)


def tokens_used_since(db: Session, user_id: str, since: datetime) -> float:
    return _used_milli(db, user_id, since) / MILLI


def _reserved_query(user_id: str):
    # Expired intents remain charged until the reaper atomically replaces them with ledger entries.
    return select(func.coalesce(func.sum(UsageReservation.reserved_amount), 0)).where(
        UsageReservation.user_id == user_id, UsageReservation.status == UsageStatus.OPEN
    )


def _reserved_milli(db: Session, user_id: str) -> int:
    return int(db.scalar(_reserved_query(user_id)) or 0)


def usage_summary(db: Session, user_id: str) -> dict:
    limits = user_limits(db, user_id)
    windows = quota_windows(limits=limits)
    # One statement sees reservation and settlement at the same database snapshot.
    # Separate SELECTs can count one call twice while it moves between the two tables.
    amounts = db.execute(
        select(
            _reserved_query(user_id).scalar_subquery(),
            *(_used_query(user_id, start, reset).scalar_subquery() for start, reset, _ in windows.values()),
        )
    ).one()
    reserved = int(amounts[0])
    out: dict = {"alerts": []}
    for (name, (start, reset, limit)), amount in zip(windows.items(), amounts[1:], strict=True):
        used = int(amount)
        total = used + reserved
        percent = round(total / (limit * MILLI) * 100, 2) if limit else (100.0 if total else 0.0)
        out[name] = {"used": used / MILLI, "reserved": reserved / MILLI, "limit": limit, "percent": percent, "resets_at": reset.isoformat()}
        code = "exceeded" if total >= limit * MILLI else "near_limit" if total * 5 >= limit * MILLI * 4 else None
        if code:
            out["alerts"].append({"code": code, "window": name, "resets_at": reset.isoformat()})
    out["library_bytes"] = {"used": library_bytes(db, user_id), "limit": limits.library_bytes}
    out["resets_at_utc"] = windows["daily_tokens"][1].isoformat()  # existing Portal clients
    return out


def enforce_token_quota(db: Session, user_id: str, incoming_milli: int = 0) -> None:
    reserved = _reserved_milli(db, user_id)
    for name, (start, reset, limit) in quota_windows(limits=user_limits(db, user_id)).items():
        total = _used_milli(db, user_id, start, reset) + reserved
        if total + incoming_milli > limit * MILLI or (not incoming_milli and total >= limit * MILLI):
            metrics.QUOTA_REJECTIONS.labels(name).inc()
            raise _error(429, UsageErrorCode.QUOTA_EXCEEDED, window=name, resets_at=reset.isoformat())


def reserve_usage(db: Session, body: ReserveUsageIn) -> UsageReservation:
    run = db.get(Run, body.run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    try:
        with user_critical_section(db, run.user_id):
            old = db.get(UsageReservation, body.call_id, populate_existing=True)
            if old:
                if any(getattr(old, key) != value for key, value in body.model_dump().items()):
                    raise _error(409, UsageErrorCode.CALL_CONFLICT)
                db.commit()
                return old
            if db.scalar(select(ModelUsage.id).where(ModelUsage.call_id == body.call_id)) is not None:
                raise _error(409, UsageErrorCode.CALL_CONFLICT)
            db.refresh(run)
            if run.status not in RunStatus.ACTIVE:
                raise _error(409, UsageErrorCode.RUN_NOT_ACTIVE)
            mode = (run.options or {}).get("mode", "work")
            if body.mode != mode:
                raise _error(409, UsageErrorCode.CALL_CONFLICT, message="mode differs from run")
            spec = get_catalog().get(body.alias)
            if spec is None or not spec.available:
                raise _error(400, UsageErrorCode.MODEL_UNAVAILABLE)
            weight, cached = snapshot_weight(spec.weight), snapshot_weight(settings.cached_token_weight)
            amount = reserved_amount(body.estimated_input_tokens, body.max_output_tokens, weight, cached)
            enforce_token_quota(db, run.user_id, amount)
            now = datetime.now(UTC)
            reservation = UsageReservation(
                **body.model_dump(),
                user_id=run.user_id,
                weight=weight,
                cached_token_weight=cached,
                policy_version=POLICY_VERSION,
                reserved_amount=amount,
                status=UsageStatus.OPEN,
                created_at=now,
                expires_at=now + timedelta(seconds=settings.usage_reservation_ttl_seconds),
            )
            db.add(reservation)
            db.commit()
            return reservation
    except IntegrityError as exc:
        db.rollback()
        raise _error(409, UsageErrorCode.CALL_CONFLICT) from exc
    except Exception:
        db.rollback()
        raise


def run_usage_totals(db: Session, run_id: str) -> dict[str, int]:
    columns = ("prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens", "weighted_amount")
    row = db.execute(
        select(*(func.coalesce(func.sum(getattr(ModelUsage, name)), 0) for name in columns), func.count(ModelUsage.id)).where(
            ModelUsage.run_id == run_id
        )
    ).one()
    return dict(zip((*columns, "calls"), (int(v) for v in row), strict=True))


def _write_usage(db: Session, reservation: UsageReservation, body: SettleUsageIn | None) -> ModelUsage:
    row = db.scalar(select(ModelUsage).where(ModelUsage.call_id == reservation.call_id))
    if row is not None and row.status != UsageStatus.UNRECONCILED:
        return row
    if row is None:
        owner = db.get(User, reservation.user_id)
        row = ModelUsage(
            call_id=reservation.call_id,
            run_id=reservation.run_id,
            user_id=reservation.user_id,
            dept_id=owner.dept_id if owner else None,  # snapshot: a later move does not move this usage
            attempt_id=reservation.attempt_id,
            mode=reservation.mode,
            alias=reservation.alias,
            model=reservation.alias,
            weight=float(reservation.weight),
            cached_token_weight=reservation.cached_token_weight,
            policy_version=reservation.policy_version,
            created_at=reservation.created_at,
        )
        db.add(row)
    if body is None:
        row.prompt_tokens = reservation.estimated_input_tokens
        row.completion_tokens = reservation.max_output_tokens
        row.cached_tokens = row.reasoning_tokens = 0
        row.actual_backend = ""
        row.weighted_amount = reservation.reserved_amount
        row.status = UsageStatus.UNRECONCILED
    else:
        row.prompt_tokens, row.completion_tokens = body.input_tokens, body.output_tokens
        row.cached_tokens, row.reasoning_tokens = body.cached_tokens, body.reasoning_tokens
        row.actual_backend, row.status = body.actual_backend, body.status
        row.weighted_amount = weighted_amount(
            body.input_tokens, body.cached_tokens, body.output_tokens, reservation.weight, reservation.cached_token_weight
        )
    reservation.status = row.status
    db.flush()
    run = db.get(Run, reservation.run_id, populate_existing=True)
    run.result = {**(run.result or {}), "usage_totals": run_usage_totals(db, run.id)}
    db.flush()
    return row


def settle_usage(db: Session, body: SettleUsageIn) -> ModelUsage:
    reservation = db.get(UsageReservation, body.call_id)
    if reservation is None:
        raise _error(404, UsageErrorCode.RESERVATION_NOT_FOUND)
    try:
        with user_critical_section(db, reservation.user_id):
            db.refresh(reservation)
            existing = db.scalar(select(ModelUsage.id).where(ModelUsage.call_id == body.call_id))
            row = _write_usage(db, reservation, body)
            db.commit()
            if existing is None:
                model = metrics.model_label(row.alias)
                metrics.MODEL_TOKENS.labels(model, "prompt").inc(row.prompt_tokens)
                metrics.MODEL_TOKENS.labels(model, "completion").inc(row.completion_tokens)
                metrics.MODEL_TOKENS.labels(model, "cached").inc(row.cached_tokens)
            return row
    except Exception:
        db.rollback()
        raise


def expire_reservations(db: Session, now: datetime, batch_size: int = 200) -> int:
    ids = list(
        db.scalars(
            select(UsageReservation.call_id)
            .where(UsageReservation.status == UsageStatus.OPEN, UsageReservation.expires_at <= now)
            .order_by(UsageReservation.expires_at, UsageReservation.call_id)
            .limit(batch_size)
        )
    )
    count = 0
    for call_id in ids:
        reservation = db.get(UsageReservation, call_id, populate_existing=True)
        try:
            with user_critical_section(db, reservation.user_id):
                db.refresh(reservation)
                if reservation.status == UsageStatus.OPEN:
                    _write_usage(db, reservation, None)
                    count += 1
                db.commit()
        except Exception:
            db.rollback()
            raise
    return count


def library_bytes(db: Session, user_id: str) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(FileObject.size), 0)).where(
                FileObject.user_id == user_id, FileObject.status != FileStatus.REJECTED
            )
        )
        or 0
    )


def enforce_library_quota(db: Session, user_id: str, incoming: int) -> None:
    if library_bytes(db, user_id) + incoming > user_limits(db, user_id).library_bytes:
        metrics.QUOTA_REJECTIONS.labels("library").inc()
        raise HTTPException(status_code=413, detail="library quota exceeded; delete files or ask for more space")
