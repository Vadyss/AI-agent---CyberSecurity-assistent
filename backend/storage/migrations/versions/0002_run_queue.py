# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Run queue: retry backoff and at most one active run per tenant.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("not_before", sa.DateTime(timezone=True), nullable=True))
    op.create_index("uq_runs_one_active_per_tenant", "runs", ["tenant_id"], unique=True,
                    postgresql_where=sa.text("status IN ('running', 'awaiting_approval')"))


def downgrade() -> None:
    op.drop_index("uq_runs_one_active_per_tenant", table_name="runs")
    op.drop_column("runs", "not_before")
