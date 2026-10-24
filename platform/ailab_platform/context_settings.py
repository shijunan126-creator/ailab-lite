"""One primary-key read per bootstrap; frozen for the run, no polling or process cache drift."""

from .models import PlatformSetting
from .schemas import ContextSettings


def context_settings(db) -> dict:
    row = db.get(PlatformSetting, "context")
    return ContextSettings.model_validate(row.value if row else {}).model_dump()
