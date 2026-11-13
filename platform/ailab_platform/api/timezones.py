"""Which wall clock a user's day is on."""

from sqlalchemy import select

from ..config import settings
from ..models import ScheduledTask


def user_timezone(db, user) -> str:
    """The zone the user last scheduled in, else the one memory consolidation uses, else the platform default."""
    latest = db.scalar(
        select(ScheduledTask.timezone).where(ScheduledTask.user_id == user.id).order_by(ScheduledTask.updated_at.desc()).limit(1)
    )
    from ..memory_dream.settings import dream_settings

    return latest or dream_settings(user).timezone or settings.memory_dream_default_timezone
