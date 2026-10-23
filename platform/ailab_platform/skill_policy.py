"""Effective governance: global denials cannot be overridden by a department."""

from sqlalchemy import select

from .agent.prompts import skills
from .models import SkillPolicy


def effective_policy(db, user):
    scopes = ["global"] + ([user.dept_id] if user.dept_id else [])
    rows = list(db.scalars(select(SkillPolicy).where(SkillPolicy.scope.in_(scopes))))
    denied = {r.name for r in rows if r.name != "*personal*" and r.state == "disabled"}
    roles = user.principal.roles
    denied.update(n for n, m in skills().items() if m.get("roles") and not roles.intersection(m["roles"]))
    rank = {"allowed": 0, "review": 1, "disabled": 2}
    personal = max((r.state for r in rows if r.name == "*personal*"), key=rank.get, default="allowed")
    return denied, personal


def run_disabled_skills(db, user):
    denied, _ = effective_policy(db, user)
    return sorted(denied | set((user.settings or {}).get("disabled_skills") or []))
