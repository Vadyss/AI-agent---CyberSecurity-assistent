# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Schema guarantees, checked on both SQLite and migrated PostgreSQL."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, StatementError

from storage.db import Database, head_revision
from storage.models import ApiKey, LlmUsage, Membership, Run, RunEvent, Tenant, User


async def make_tenant(database, slug="acme") -> Tenant:
    async with database.session() as session:
        tenant = Tenant(slug=slug, name=slug.title())
        session.add(tenant)
    return tenant


async def test_tenant_deletion_cascades_to_every_tenant_row(database):
    tenant = await make_tenant(database)
    other = await make_tenant(database, "other")
    async with database.session() as session:
        user = User(issuer="https://idp.example", subject="u1", email="a@example.test", email_verified=True)
        session.add(user)
        await session.flush()
        session.add_all([
            Membership(tenant_id=tenant.id, user_id=user.id, role="admin"),
            Membership(tenant_id=other.id, user_id=user.id, role="viewer"),
            ApiKey(id="k1", tenant_id=tenant.id, name="ci", role="analyst", secret_sha256="0" * 64),
            Run(id="run_aaaa1111", tenant_id=tenant.id, created_by="user:x", request="detect"),
        ])
        await session.flush()
        session.add_all([RunEvent(run_id="run_aaaa1111", seq=1, payload={"type": "run_started"}),
                         LlmUsage(tenant_id=tenant.id, run_id="run_aaaa1111", step="planner", model="m",
                                  cost_source="provider", status="success")])
    async with database.session() as session:
        await session.delete(await session.get(Tenant, tenant.id))
    async with database.session() as session:
        for model in (ApiKey, Run, RunEvent, LlmUsage):
            assert (await session.execute(sa.select(sa.func.count()).select_from(model))).scalar_one() == 0
        memberships = (await session.execute(sa.select(Membership))).scalars().all()
        # The user and their membership in the other tenant survive.
        assert [(m.tenant_id, m.role) for m in memberships] == [(other.id, "viewer")]


@pytest.mark.parametrize("model,values", [
    (Membership, {"role": "owner"}),
    (ApiKey, {"role": "admin"}),
])
async def test_role_constraints_are_enforced_by_the_database(database, model, values):
    tenant = await make_tenant(database)
    with pytest.raises(IntegrityError):
        async with database.session() as session:
            user = User(issuer="i", subject=uuid.uuid4().hex)
            session.add(user)
            await session.flush()
            if model is Membership:
                session.add(Membership(tenant_id=tenant.id, user_id=user.id, **values))
            else:
                session.add(ApiKey(id="k2", tenant_id=tenant.id, name="n", secret_sha256="0" * 64, **values))


async def test_run_status_is_constrained(database):
    tenant = await make_tenant(database)
    with pytest.raises(IntegrityError):
        async with database.session() as session:
            session.add(Run(id="run_bbbb2222", tenant_id=tenant.id, created_by="x", request="r", status="paused"))


async def test_users_are_unique_per_issuer_and_subject(database):
    with pytest.raises(IntegrityError):
        async with database.session() as session:
            session.add_all([User(issuer="i", subject="same"), User(issuer="i", subject="same")])


async def test_money_and_time_round_trip_exactly(database):
    tenant = await make_tenant(database)
    moment = datetime(2026, 10, 10, 12, 0, 0, 123000, tzinfo=timezone.utc)
    async with database.session() as session:
        session.add(LlmUsage(tenant_id=tenant.id, step="forge", model="m", cost_usd=Decimal("0.000123456789"),
                             cost_source="provider", status="success", created_at=moment))
    async with database.session() as session:
        row = (await session.execute(sa.select(LlmUsage))).scalar_one()
        assert row.cost_usd == Decimal("0.000123456789")
        assert row.created_at == moment and row.created_at.tzinfo is not None


async def test_naive_datetimes_are_refused(database):
    tenant = await make_tenant(database)
    with pytest.raises(StatementError):
        async with database.session() as session:
            session.add(Run(id="run_cccc3333", tenant_id=tenant.id, created_by="x", request="r",
                            created_at=datetime(2026, 1, 1)))


def test_migrations_match_the_models_and_downgrade_cleanly(postgres_url):
    backend = Path(__file__).resolve().parents[2]
    env = {**os.environ, "DATABASE_URL": postgres_url}
    for command in (["check"], ["downgrade", "base"], ["upgrade", "head"], ["check"]):
        subprocess.run([sys.executable, "-m", "alembic", *command], cwd=backend, env=env, check=True)


async def test_startup_detects_an_outdated_schema(postgres_url):
    db = Database(postgres_url, pool_size=1)
    try:
        assert await db.schema_is_current() is True
        async with db.engine.begin() as connection:
            await connection.execute(sa.text("UPDATE alembic_version SET version_num = 'stale'"))
        assert await db.schema_is_current() is False
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(sa.text("UPDATE alembic_version SET version_num = :head"), {"head": head_revision()})
        await db.close()


def test_only_postgres_or_sqlite_urls_are_accepted():
    with pytest.raises(ValueError):
        Database("mysql://user@host/db")
