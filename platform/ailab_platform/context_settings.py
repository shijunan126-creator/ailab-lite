"""One primary-key read per bootstrap; frozen for the run, no polling or process cache drift."""

from .config import ContextBandSettings, settings
from .models import PlatformSetting
from .schemas import ContextSettings


def context_settings(db) -> dict:
    row = db.get(PlatformSetting, "context")
    return ContextSettings.model_validate(
        {**{key: getattr(settings, key) for key in ContextBandSettings.model_fields}, **(row.value if row else {})}
    ).model_dump()
