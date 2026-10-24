"""Database models.

Platform API is the only writer; Agent / Broker / Worker go through the
internal API instead of importing this module.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from . import naming
from .contracts.jobs import RunStatus as RunStatus


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class FileStatus:
    PENDING = "pending"
    READY = "ready"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_issuer_subject", "issuer", "external_subject", unique=True),
        Index("ix_users_created_id", "created_at", "id"),
        Index("ix_users_dept_created_id", "dept_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("usr"))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    # Password login. A user without them can only be reached with an API token.
    username: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # per-user preferences, e.g. {"disabled_skills": ["pptx"]}
    settings: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    issuer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    external_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dept_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    quota_policy_id: Mapped[str | None] = mapped_column(ForeignKey("quota_policies.id"), index=True, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tokens: Mapped[list[ApiToken]] = relationship(back_populates="user")
    role_rows: Mapped[list[UserRole]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    department: Mapped[Department | None] = relationship(lazy="joined")
    quota_policy: Mapped[QuotaPolicy | None] = relationship(lazy="joined")

    @property
    def principal(self):
        from .contracts.identity import Principal

        return Principal(
            self.id, self.issuer, self.external_subject, self.dept_id, frozenset(r.role for r in self.role_rows) or frozenset({"user"})
        )

    @property
    def default_quota_policy(self):
        from sqlalchemy import select
        from sqlalchemy.orm import object_session

        db = object_session(self)
        return db.scalar(select(QuotaPolicy).where(QuotaPolicy.is_default.is_(True))) if db else None

    @property
    def quota_fallback(self):
        from .config import settings
        from .domain.quota_policy import Limits

        return Limits(
            settings.daily_token_limit, settings.monthly_token_limit, settings.library_quota_bytes, settings.max_active_runs_per_user
        )


class QuotaPolicy(Base):
    __tablename__ = "quota_policies"
    __table_args__ = (
        Index(
            "uq_quota_policies_default",
            "is_default",
            unique=True,
            sqlite_where=text("is_default = 1"),
            postgresql_where=text("is_default = true"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("qpol"))
    name: Mapped[str] = mapped_column(String(120))
    daily_tokens: Mapped[int] = mapped_column(BigInteger)
    monthly_tokens: Mapped[int] = mapped_column(BigInteger)
    library_bytes: Mapped[int] = mapped_column(BigInteger)
    max_concurrent_runs: Mapped[int] = mapped_column(Integer)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("dept"))
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), index=True, nullable=True)
    quota_policy_id: Mapped[str | None] = mapped_column(ForeignKey("quota_policies.id"), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    quota_policy: Mapped[QuotaPolicy | None] = relationship(lazy="joined")


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (Index("ix_user_roles_role_user", "role", "user_id"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_at_id", "at", "id"),
        Index("ix_audit_request_at", "request_id", "at"),
        Index("ix_audit_actor_at_id", "actor_id", "at", "id"),
        Index("ix_audit_action_at_id", "action", "at", "id"),
        Index("ix_audit_target_at_id", "target_type", "target_id", "at", "id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("aud"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_service: Mapped[str | None] = mapped_column(String(80), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), default="success")
    request_id: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class OIDCFlow(Base):
    """Short-lived server-side PKCE material; a separate secret binds the flow to the browser."""

    __tablename__ = "oidc_flows"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    binding_hash: Mapped[str] = mapped_column(String(64))
    verifier: Mapped[str] = mapped_column(String(128))
    nonce: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("tok"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="default")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # None: never (API tokens)

    user: Mapped[User] = relationship(back_populates="tokens")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_user_updated", "user_id", "updated_at"),
        Index("uq_projects_user_name", "user_id", "name_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("prj"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(
        String(64), server_default="", default=lambda ctx: naming.name_key(ctx.get_current_parameters()["name"])
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conversation(Base):
    """A chat thread. Each user message starts one Run (a turn); turns share history and a sandbox workspace."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
        Index("ix_conversations_user_pinned", "user_id", "pinned_at", "updated_at"),
        Index(
            "ix_conversations_user_search",
            "user_id",
            "updated_at",
            "id",
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_conversations_deleted", "deleted_at", "id"),
        Index(
            "ix_conversations_user_project_live",
            "user_id",
            "project_id",
            sqlite_where=text("deleted_at IS NULL AND project_id IS NOT NULL"),
            postgresql_where=text("deleted_at IS NULL AND project_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cnv"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True, nullable=True)
    active_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    forked_from: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_through_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(120), default="")  # the last model picked here
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_conversation_parent", "conversation_id", "parent_run_id", "created_at", "id"),
        Index("ix_runs_conversation_id_id", "conversation_id", "id"),
        Index("ix_runs_conversation_created_id", "conversation_id", "created_at", "id"),
        Index("ix_runs_user_created", "user_id", "created_at"),
        Index("ix_runs_waiting", "status", "waiting_since", "id"),
        Index("ix_runs_finished", "status", "finished_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("run"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), index=True, nullable=True)
    superseded_by_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    edited_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    retry_of: Mapped[str | None] = mapped_column(String(40), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED, index=True)
    model: Mapped[str] = mapped_column(String(120), default="")
    input_file_ids: Mapped[list] = mapped_column(JSON, default=list)
    # how the user asked: {"mode": "chat" | "work", "skill": "pptx" | ...} (empty for runs from before)
    options: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkpoint_step: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    runtime_meta: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    work_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=0)
    work_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    events: Mapped[list[RunEvent]] = relationship(back_populates="run", cascade="all, delete-orphan", order_by="RunEvent.seq")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="run")


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),
        Index("ix_run_events_created", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="events")


class FileObject(Base):
    __tablename__ = "files"
    __table_args__ = (
        Index("ix_files_user_created", "user_id", "created_at"),
        Index("ix_files_status_created", "status", "created_at", "id"),
        Index("ix_files_parent_id", "parent_id"),
        Index("uq_files_lineage_version", "lineage_id", "version", unique=True),
        Index("uq_files_lineage_head", "lineage_id", sqlite_where=text("is_head = 1"), postgresql_where=text("is_head = 1"), unique=True),
        Index("ix_files_lineage_retained", "lineage_id", "purged_at", "version"),
        Index(
            "ix_files_live_created",
            "user_id",
            "created_at",
            "id",
            "is_head",
            "deleted_at",
            "purged_at",
            sqlite_where=text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"),
            postgresql_where=text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"),
        ),
        Index(
            "ix_files_live_project",
            "user_id",
            "project_id",
            "created_at",
            "id",
            "is_head",
            "deleted_at",
            "purged_at",
            sqlite_where=text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"),
            postgresql_where=text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"),
        ),
        Index(
            "ix_files_live_starred",
            "user_id",
            "starred_at",
            "id",
            "is_head",
            "deleted_at",
            "purged_at",
            sqlite_where=text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL AND starred_at IS NOT NULL"),
            postgresql_where=text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL AND starred_at IS NOT NULL"),
        ),
        Index(
            "ix_files_live_images",
            "user_id",
            "created_at",
            "id",
            "is_head",
            "content_type",
            "deleted_at",
            "purged_at",
            sqlite_where=text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL AND content_type LIKE 'image/%'"),
            postgresql_where=text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL AND content_type LIKE 'image/%'"),
        ),
        Index(
            "ix_files_trash",
            "user_id",
            "deleted_at",
            "id",
            sqlite_where=text("is_head = 1 AND deleted_at IS NOT NULL"),
            postgresql_where=text("is_head = 1 AND deleted_at IS NOT NULL"),
        ),
        Index(
            "ix_files_purge_due",
            "purge_at",
            "user_id",
            "lineage_id",
            sqlite_where=text("is_head = 1 AND deleted_at IS NOT NULL"),
            postgresql_where=text("is_head = 1 AND deleted_at IS NOT NULL"),
        ),
        Index(
            "ix_files_user_starred",
            "user_id",
            "starred_at",
            sqlite_where=text("starred_at IS NOT NULL"),
            postgresql_where=text("starred_at IS NOT NULL"),
        ),
        Index("ix_files_user_project_created", "user_id", "project_id", "created_at"),
        Index(
            "ix_files_images_user_created",
            "user_id",
            "created_at",
            sqlite_where=text("content_type LIKE 'image/%'"),
            postgresql_where=text("content_type LIKE 'image/%'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("fil"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True, nullable=True)
    starred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    name_key: Mapped[str] = mapped_column(
        String(64), server_default="", default=lambda ctx: naming.name_key(ctx.get_current_parameters()["filename"])
    )
    purpose: Mapped[str] = mapped_column(String(16), default="library", server_default="library")
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    storage_key: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default=FileStatus.PENDING)
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("files.id"), nullable=True)
    # No FK to the root: a lineage survives retention of its oldest object.
    lineage_id: Mapped[str] = mapped_column(String(40), default=lambda context: context.get_current_parameters()["id"], server_default="")
    is_head: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trashed_from_project_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("art"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id"))
    role: Mapped[str] = mapped_column(String(30), default="output")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="artifacts")
    file: Mapped[FileObject] = relationship()


class ModelUsage(Base):
    __tablename__ = "model_usage"
    __table_args__ = (
        Index("ix_model_usage_run_created", "run_id", "created_at"),
        Index("ix_model_usage_context", "run_id", "quiet", "status", "created_at", "id"),
        Index("ix_model_usage_user_created", "user_id", "created_at"),
        Index("ix_model_usage_status_id", "status", "id"),
        Index("ix_model_usage_call_id", "call_id", unique=True),
        Index("ix_model_usage_created_dept", "created_at", "dept_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Not a foreign key: the ledger outlives the runs it records (a purged conversation's usage still counts).
    run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    quiet: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    subject: Mapped[str] = mapped_column(String(20), default="run", server_default="run")
    file_id: Mapped[str | None] = mapped_column(String(40))
    call_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    dept_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    attempt_id: Mapped[str] = mapped_column(String(80), default="", server_default="")
    mode: Mapped[str] = mapped_column(String(20), default="work", server_default="work")
    alias: Mapped[str] = mapped_column(String(120), default="", server_default="")
    actual_backend: Mapped[str] = mapped_column(String(120), default="", server_default="")
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cached_token_weight: Mapped[Decimal] = mapped_column(Numeric(20, 9), default=Decimal("0.1"), server_default="0.1")
    weighted_amount: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    policy_version: Mapped[str] = mapped_column(String(40), default="legacy", server_default="legacy")
    status: Mapped[str] = mapped_column(String(20), default="settled", server_default="settled")
    model: Mapped[str] = mapped_column(String(120), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Part of prompt_tokens that the upstream served from its prompt cache (billed far lower).
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The model's quota weight *when the call was recorded*. Changing a weight in models.toml later
    # must not rewrite what past calls cost.
    weight: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LifecycleDeletion(Base):
    """Durable object cleanup intent, committed with deletion of the business owner."""

    __tablename__ = "lifecycle_deletions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(500), unique=True)
    kind: Mapped[str] = mapped_column(String(30))
    is_prefix: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsageReservation(Base):
    __tablename__ = "usage_reservations"
    __table_args__ = (
        Index("ix_usage_reservations_user_status", "user_id", "status"),
        Index("ix_usage_reservations_status_expires", "status", "expires_at", "call_id"),
        Index("ix_usage_reservations_run", "run_id"),
    )

    call_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(40))
    quiet: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    subject: Mapped[str] = mapped_column(String(20), default="run", server_default="run")
    file_id: Mapped[str | None] = mapped_column(String(40))
    user_id: Mapped[str] = mapped_column(String(40))
    attempt_id: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(20))
    alias: Mapped[str] = mapped_column(String(120))
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    estimated_input_tokens: Mapped[int] = mapped_column(Integer)
    weight: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    cached_token_weight: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    policy_version: Mapped[str] = mapped_column(String(40))
    reserved_amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxMessage(Base):
    """Durable jobs retained through publication until an authenticated consumer ACK."""

    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    topic: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default="1970-01-01 00:00:00")
    last_error: Mapped[str | None] = mapped_column(String(120))
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[str | None] = mapped_column(String(40))
    deliveries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    needs_attention: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    __table_args__ = (
        Index("ix_outbox_claim_token", "claim_token"),
        Index("ix_outbox_due", "needs_attention", "next_attempt_at", "id"),
        Index("ix_outbox_created_at", "created_at", "id"),
    )


class RunAttempt(Base):
    __tablename__ = "run_attempts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    agent_instance: Mapped[str] = mapped_column(String(120))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = (UniqueConstraint("run_id", "number"),)


class SkillPolicy(Base):
    """Global (scope='global') or department policy for a built-in skill, or '*personal*' for personal skills."""

    __tablename__ = "skill_policies"
    scope: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(16))


class SkillApproval(Base):
    """A platform admin's decision on one personal skill file while its owner's policy is 'review'."""

    __tablename__ = "skill_approvals"
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), primary_key=True)
    state: Mapped[str] = mapped_column(String(16))


# The review queue must spell the predicate exactly like the sparse index so both planners can use it.
REVIEW_PREDICATES = {
    "sqlite": "json_extract(meta, '$.skill.name') IS NOT NULL AND status = 'ready' "
    "AND is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL",
    "postgresql": "(meta -> 'skill' ->> 'name') IS NOT NULL AND status = 'ready' "
    "AND is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL",
}
Index(
    "ix_files_skill_review",
    FileObject.id,
    sqlite_where=text(REVIEW_PREDICATES["sqlite"]),
    postgresql_where=text(REVIEW_PREDICATES["postgresql"]),
)


class RunCheckpoint(Base):
    __tablename__ = "run_checkpoints"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    step: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(40))
    state_key: Mapped[str] = mapped_column(String(500), index=True)
    plan: Mapped[list] = mapped_column(JSON, default=list)
    tool_log: Mapped[dict] = mapped_column(JSON, default=dict)
    deliveries: Mapped[dict] = mapped_column(JSON, default=dict)
    runtime_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_run_checkpoints_expiry", "expires_at", "run_id", "step"),)


class RunDelivery(Base):
    __tablename__ = "run_deliveries"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    delivery_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)


Index(
    "ix_files_folder_listing",
    FileObject.user_id,
    FileObject.purpose,
    FileObject.is_head,
    FileObject.deleted_at,
    FileObject.purged_at,
    FileObject.project_id,
    FileObject.created_at,
    FileObject.id,
)
Index(
    "uq_files_folder_name",
    FileObject.user_id,
    FileObject.purpose,
    func.coalesce(FileObject.project_id, ""),
    FileObject.name_key,
    unique=True,
    sqlite_where=text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"),
    postgresql_where=text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"),
)


class PlatformSetting(Base):
    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
