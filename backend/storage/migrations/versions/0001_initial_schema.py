# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Initial schema: tenants, identities, API keys, runs, run events and LLM usage.

Revision ID: 0001
Revises: 
Create Date: 2026-10-10 15:50:24.554654
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('tenants',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('slug', sa.String(length=63), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('monthly_budget_usd', sa.Numeric(precision=24, scale=12), nullable=True),
    sa.Column('max_runs_per_day', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('active', 'suspended')", name=op.f('ck_tenants_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tenants')),
    sa.UniqueConstraint('slug', name=op.f('uq_tenants_slug'))
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('issuer', sa.String(length=500), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('email_verified', sa.Boolean(), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('issuer', 'subject', name='uq_users_issuer_subject')
    )
    op.create_table('api_keys',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('secret_sha256', sa.String(length=64), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("role IN ('analyst', 'viewer')", name=op.f('ck_api_keys_role')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_api_keys_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_api_keys_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_api_keys'))
    )
    op.create_index(op.f('ix_api_keys_tenant_id'), 'api_keys', ['tenant_id'], unique=False)
    op.create_table('invitations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('invited_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("role IN ('admin', 'analyst', 'viewer')", name=op.f('ck_invitations_role')),
    sa.ForeignKeyConstraint(['invited_by'], ['users.id'], name=op.f('fk_invitations_invited_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_invitations_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invitations')),
    sa.UniqueConstraint('tenant_id', 'email', name='uq_invitations_tenant_email')
    )
    op.create_index(op.f('ix_invitations_email'), 'invitations', ['email'], unique=False)
    op.create_table('memberships',
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("role IN ('admin', 'analyst', 'viewer')", name=op.f('ck_memberships_role')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_memberships_tenant_id_tenants'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_memberships_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('tenant_id', 'user_id', name=op.f('pk_memberships'))
    )
    op.create_index(op.f('ix_memberships_user_id'), 'memberships', ['user_id'], unique=False)
    op.create_table('runs',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('created_by', sa.String(length=120), nullable=False),
    sa.Column('request', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('retry_of', sa.String(length=40), nullable=True),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('reason_code', sa.String(length=40), nullable=True),
    sa.Column('last_seq', sa.Integer(), nullable=False),
    sa.Column('llm_calls', sa.Integer(), nullable=False),
    sa.Column('tokens_total', sa.BigInteger(), nullable=True),
    sa.Column('cost_usd', sa.Numeric(precision=24, scale=12), nullable=True),
    sa.Column('cost_unknown', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('queued', 'running', 'awaiting_approval', 'approved', 'rejected', 'failed', 'cancelled')", name=op.f('ck_runs_status')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_runs_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_runs'))
    )
    op.create_index('ix_runs_status_created', 'runs', ['status', 'created_at'], unique=False)
    op.create_index('ix_runs_tenant_created', 'runs', ['tenant_id', 'created_at'], unique=False)
    op.create_table('llm_usage',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.String(length=40), nullable=True),
    sa.Column('step', sa.String(length=40), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=False),
    sa.Column('prompt_version', sa.String(length=80), nullable=True),
    sa.Column('input_tokens', sa.BigInteger(), nullable=True),
    sa.Column('output_tokens', sa.BigInteger(), nullable=True),
    sa.Column('total_tokens', sa.BigInteger(), nullable=True),
    sa.Column('cost_usd', sa.Numeric(precision=24, scale=12), nullable=True),
    sa.Column('cost_source', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name=op.f('fk_llm_usage_run_id_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_llm_usage_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_llm_usage'))
    )
    op.create_index('ix_llm_usage_tenant_created', 'llm_usage', ['tenant_id', 'created_at'], unique=False)
    op.create_table('run_events',
    sa.Column('run_id', sa.String(length=40), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], name=op.f('fk_run_events_run_id_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('run_id', 'seq', name=op.f('pk_run_events'))
    )


def downgrade() -> None:
    op.drop_table('run_events')
    op.drop_index('ix_llm_usage_tenant_created', table_name='llm_usage')
    op.drop_table('llm_usage')
    op.drop_index('ix_runs_tenant_created', table_name='runs')
    op.drop_index('ix_runs_status_created', table_name='runs')
    op.drop_table('runs')
    op.drop_index(op.f('ix_memberships_user_id'), table_name='memberships')
    op.drop_table('memberships')
    op.drop_index(op.f('ix_invitations_email'), table_name='invitations')
    op.drop_table('invitations')
    op.drop_index(op.f('ix_api_keys_tenant_id'), table_name='api_keys')
    op.drop_table('api_keys')
    op.drop_table('users')
    op.drop_table('tenants')
