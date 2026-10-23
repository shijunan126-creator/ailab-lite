"""Agent side of skill attestation: a skill loads only when the leased sandbox reports this Agent's exact index."""

import logging

from ..clients import ClientError
from ..config import settings
from ..skill_index import checked_index

log = logging.getLogger("ailab.agent")

REFUSED = "技能清单与沙箱镜像不一致，已拒绝加载；请改用沙箱里的 Python 库完成"


class SkillRefused(RuntimeError):
    """Raised when the leased sandbox cannot prove the skill set; the message is the tool error the model sees."""


async def verify_sandbox_skills(broker, lease_id: str) -> None:
    try:
        response = await broker._send(broker._req("GET", "/internal/skills/index", params={"lease_id": lease_id}, timeout=35))
    except ClientError as exc:
        log.error("skill attestation unavailable: broker answered %s", exc.status)
        raise SkillRefused(REFUSED) from exc
    data = response.json()
    if (
        data.get("verified") is not True
        or data.get("lease_id") != lease_id
        or not isinstance(data.get("provider_ref"), str)
        or not data["provider_ref"]
        or not isinstance(data.get("image_ref"), str)
        or not data["image_ref"]
    ):
        log.error("skill attestation response is not bound to this lease")
        raise SkillRefused(REFUSED)
    try:
        expected = checked_index(settings.skills_path)
    except (ValueError, OSError) as exc:
        log.error("agent skill source is inconsistent; refusing to load skills")
        raise SkillRefused(REFUSED) from exc
    if data.get("index") != expected:
        log.error("skill_index_mismatch between agent and leased sandbox")
        raise SkillRefused(REFUSED)
