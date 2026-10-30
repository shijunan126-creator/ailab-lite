"""Authenticated, bounded, metrics-only client rendering telemetry."""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import metrics
from ..accounts import LoginThrottle
from ..deps import current_user
from ..models import User

router = APIRouter(prefix="/v1/genui", tags=["genui"])
telemetry_throttle = LoginThrottle(10, 60)


class TelemetryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    outcome: Literal["rendered", "fallback", "error", "action"]
    component: str | None = Field(default=None, max_length=100)
    first_render_ms: float | None = Field(default=None, ge=0, le=3600000)


@router.post("/telemetry", status_code=204)
async def telemetry(body: Annotated[list[TelemetryItem], Body(max_length=50)], user: User = Depends(current_user)) -> Response:
    # No await between check and consume: requests on this API event loop cannot
    # race. Like login throttling, the quota is in memory and per API process.
    if wait := telemetry_throttle.retry_after(user.id):
        raise HTTPException(429, "GenUI telemetry rate limit", headers={"Retry-After": str(wait)})
    telemetry_throttle.fail(user.id)
    for item in body:
        # component is accepted for wire compatibility but deliberately never
        # labels the client metrics, whose contract has only outcome labels.
        metrics.GENUI_CLIENT.labels(item.outcome).inc()
        if item.first_render_ms is not None:
            metrics.GENUI_FIRST_RENDER_SECONDS.observe(item.first_render_ms / 1000)
    return Response(status_code=204)
