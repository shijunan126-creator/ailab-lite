"""Identity, departments, policy and transactional audit.

Revision ID: 0013
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quota_policies",
        sa.Column("id", sa.String(length=40), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=120), primary_key=False, nullable=False),
        sa.Column("daily_tokens", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("monthly_tokens", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("library_bytes", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("max_concurrent_runs", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("is_default", sa.Boolean(), primary_key=False, nullable=False, server_default="false"),
    )
    op.create_index(
        "uq_quota_policies_default",
        "quota_policies",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
        postgresql_where=sa.text("is_default = true"),
    )
    op.create_table(
        "departments",
        sa.Column("id", sa.String(length=40), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=120), primary_key=False, nullable=False),
        sa.Column("parent_id", sa.String(length=40), sa.ForeignKey("departments.id"), primary_key=False, nullable=True),
        sa.Column("quota_policy_id", sa.String(length=40), sa.ForeignKey("quota_policies.id"), primary_key=False, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), primary_key=False, nullable=False),
    )
    op.create_index("ix_departments_parent_id", "departments", ["parent_id"], unique=False)
    op.create_index("ix_departments_quota_policy_id", "departments", ["quota_policy_id"], unique=False)
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(length=40), sa.ForeignKey("users.id"), primary_key=True, nullable=False),
        sa.Column("role", sa.String(length=32), primary_key=True, nullable=False),
    )
    op.create_index("ix_user_roles_role_user", "user_roles", ["role", "user_id"], unique=False)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=40), primary_key=True, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), primary_key=False, nullable=False),
        sa.Column("actor_id", sa.String(length=40), sa.ForeignKey("users.id"), primary_key=False, nullable=True),
        sa.Column("actor_service", sa.String(length=80), primary_key=False, nullable=True),
        sa.Column("action", sa.String(length=80), primary_key=False, nullable=False),
        sa.Column("target_type", sa.String(length=40), primary_key=False, nullable=False),
        sa.Column("target_id", sa.String(length=255), primary_key=False, nullable=True),
        sa.Column("outcome", sa.String(length=32), primary_key=False, nullable=False),
        sa.Column("request_id", sa.String(length=64), primary_key=False, nullable=False),
        sa.Column("detail", sa.JSON(), primary_key=False, nullable=False),
    )
    op.create_index("ix_audit_action_at_id", "audit_log", ["action", "at", "id"], unique=False)
    op.create_index("ix_audit_actor_at_id", "audit_log", ["actor_id", "at", "id"], unique=False)
    op.create_index("ix_audit_at_id", "audit_log", ["at", "id"], unique=False)
    op.create_index("ix_audit_request_at", "audit_log", ["request_id", "at"], unique=False)
    op.create_index("ix_audit_target_at_id", "audit_log", ["target_type", "target_id", "at", "id"], unique=False)
    op.create_table(
        "oidc_flows",
        sa.Column("state_hash", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("binding_hash", sa.String(length=64), primary_key=False, nullable=False),
        sa.Column("verifier", sa.String(length=128), primary_key=False, nullable=False),
        sa.Column("nonce", sa.String(length=128), primary_key=False, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), primary_key=False, nullable=False),
    )
    op.create_index("ix_oidc_flows_expires_at", "oidc_flows", ["expires_at"], unique=False)
    for name, type_, table in (("dept_id", sa.String(40), "departments"), ("quota_policy_id", sa.String(40), "quota_policies")):
        if op.get_bind().dialect.name == "sqlite":
            op.execute(f"ALTER TABLE users ADD COLUMN {name} VARCHAR(40) REFERENCES {table} (id)")
        else:
            op.add_column("users", sa.Column(name, type_, nullable=True))
            op.create_foreign_key(f"fk_users_{name}", "users", table, [name], ["id"])
    op.add_column("users", sa.Column("issuer", sa.String(512), nullable=True))
    op.add_column("users", sa.Column("external_subject", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("uq_users_issuer_subject", "users", ["issuer", "external_subject"], unique=True)
    op.create_index("ix_users_created_id", "users", ["created_at", "id"])
    op.create_index("ix_users_dept_created_id", "users", ["dept_id", "created_at", "id"])
    op.create_index("ix_users_quota_policy_id", "users", ["quota_policy_id"])
    op.execute("INSERT INTO user_roles (user_id, role) SELECT id, 'user' FROM users")
    op.execute("INSERT INTO user_roles (user_id, role) SELECT id, 'platform_admin' FROM users WHERE username = 'admin'")


def downgrade() -> None:
    for name in ("uq_users_issuer_subject", "ix_users_created_id", "ix_users_dept_created_id", "ix_users_quota_policy_id"):
        op.drop_index(name, table_name="users")
    for name in ("last_login_at", "external_subject", "issuer", "quota_policy_id", "dept_id"):
        if name in {"dept_id", "quota_policy_id"} and op.get_bind().dialect.name != "sqlite":
            op.drop_constraint(f"fk_users_{name}", "users", type_="foreignkey")
        op.drop_column("users", name)
    for table in ("oidc_flows", "audit_log", "user_roles", "departments", "quota_policies"):
        op.drop_table(table)
