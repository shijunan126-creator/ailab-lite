"""Administrative capability and department scope; content ownership has no admin override."""

from dataclasses import dataclass

from ..contracts.identity import Principal


@dataclass(frozen=True)
class Target:
    dept_id: str | None = None
    roles: frozenset[str] = frozenset()
    new_roles: frozenset[str] | None = None
    new_dept_id: str | None = None


class AuthorizationPolicy:
    def can(self, principal: Principal, action: str, target: Target | None = None) -> bool:
        if action not in {
            "users.list",
            "users.create",
            "users.update",
            "users.password",
            "departments.list",
            "departments.create",
            "departments.update",
            "departments.delete",
            "quota-policies.list",
            "quota-policies.create",
            "quota-policies.update",
            "quota-policies.delete",
            "audit.list",
            "usage.list",
        }:
            return False
        if "platform_admin" in principal.roles:
            return True
        if "auditor" in principal.roles and action in {"audit.list", "usage.list"}:
            return True
        if "dept_admin" not in principal.roles or principal.dept_id is None:
            return False
        if action in {"users.list", "departments.list", "quota-policies.list", "usage.list"}:
            return True  # callers MUST also apply scope to their SQL query
        if action not in {"users.create", "users.update", "users.password"} or target is None:
            return False
        return (
            target.dept_id == principal.dept_id
            and target.new_dept_id in {None, principal.dept_id}
            and not target.roles.intersection({"platform_admin", "auditor"})
            and (target.new_roles is None or target.new_roles <= {"user", "dept_admin"})
        )


policy = AuthorizationPolicy()
