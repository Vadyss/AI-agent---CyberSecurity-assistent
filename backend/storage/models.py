# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""SQLAlchemy schema. Alembic migrations in ``storage/migrations`` own the real DDL."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("admin", "analyst", "viewer")
RUN_STATUSES = ("queued", "running", "awaiting_approval", "approved", "rejected", "failed", "cancelled")
JsonColumn = sa.JSON().with_variant(JSONB(), "postgresql")
Money = sa.Numeric(24, 12)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UtcDateTime(sa.TypeDecorator):
    """Always timezone-aware UTC, also on SQLite, which drops the offset."""

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            raise ValueError("Naive datetimes are not stored.")
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(sa.String(63), unique=True)
    name: Mapped[str] = mapped_column(sa.String(200))
    status: Mapped[str] = mapped_column(sa.String(16), default="active")
    monthly_budget_usd: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    max_runs_per_day: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (sa.CheckConstraint("status IN ('active', 'suspended')", name="status"),)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    issuer: Mapped[str] = mapped_column(sa.String(500))
    subject: Mapped[str] = mapped_column(sa.String(255))
    email: Mapped[str | None] = mapped_column(sa.String(320), nullable=True)
    email_verified: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    display_name: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    __table_args__ = (sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),)


class Membership(Base):
    __tablename__ = "memberships"

    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(sa.String(16))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (sa.CheckConstraint("role IN ('admin', 'analyst', 'viewer')", name="role"),)


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("tenants.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(sa.String(320), index=True)
    role: Mapped[str] = mapped_column(sa.String(16))
    invited_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    __table_args__ = (sa.UniqueConstraint("tenant_id", "email", name="uq_invitations_tenant_email"),
                      sa.CheckConstraint("role IN ('admin', 'analyst', 'viewer')", name="role"))


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(sa.String(100))
    role: Mapped[str] = mapped_column(sa.String(16))
    secret_sha256: Mapped[str] = mapped_column(sa.String(64))
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    # API keys never carry admin rights: administration needs a human login.
    __table_args__ = (sa.CheckConstraint("role IN ('analyst', 'viewer')", name="role"),)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(sa.String(40), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("tenants.id", ondelete="CASCADE"))
    created_by: Mapped[str] = mapped_column(sa.String(120))
    request: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(24), default="queued")
    attempt: Mapped[int] = mapped_column(sa.Integer, default=1)
    retry_of: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    reason_code: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    last_seq: Mapped[int] = mapped_column(sa.Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(sa.Integer, default=0)
    tokens_total: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    cost_unknown: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    __table_args__ = (
        sa.CheckConstraint("status IN ('queued', 'running', 'awaiting_approval', 'approved', 'rejected', "
                           "'failed', 'cancelled')", name="status"),
        sa.Index("ix_runs_tenant_created", "tenant_id", "created_at"),
        sa.Index("ix_runs_status_created", "status", "created_at"),
    )


class RunEvent(Base):
    __tablename__ = "run_events"

    run_id: Mapped[str] = mapped_column(sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JsonColumn)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("tenants.id", ondelete="CASCADE"))
    run_id: Mapped[str | None] = mapped_column(sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=True)
    step: Mapped[str] = mapped_column(sa.String(40))
    model: Mapped[str] = mapped_column(sa.String(200))
    prompt_version: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    cost_source: Mapped[str] = mapped_column(sa.String(16))
    status: Mapped[str] = mapped_column(sa.String(16))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    __table_args__ = (sa.Index("ix_llm_usage_tenant_created", "tenant_id", "created_at"),)
