# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Identity rules, checked on SQLite and PostgreSQL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from identity import apikeys
from identity.principal import IdentityError, Permission, Principal
from identity.service import IdentityService
from storage.models import Tenant

ISSUER = "https://idp.test"


def claims(subject, email=None, verified=True, **extra):
    return {"sub": subject, "email": email or f"{subject}@example.test", "email_verified": verified, **extra}


@pytest.fixture
def service(database):
    return IdentityService(database, bootstrap_admin_email="Founder@Example.test", bootstrap_tenant_name="Acme SOC")


async def test_users_are_created_once_and_refreshed(service):
    first = await service.sync_user(ISSUER, claims("alice", name="Alice"))
    again = await service.sync_user(ISSUER, claims("alice", email="ALICE@new.test", name="Alice B."))
    assert first.id == again.id
    assert (again.email, again.display_name) == ("alice@new.test", "Alice B.")


async def test_bootstrap_admin_gets_the_first_tenant_once(service):
    founder = await service.sync_user(ISSUER, claims("founder", email="founder@example.test"))
    principal = await service.principal_for_user(founder, None)
    assert (principal.tenant_slug, principal.role) == ("acme-soc", "admin")
    # A different account with the same address later does not get a second tenant.
    impostor = await service.sync_user("https://other-idp.test", claims("x", email="founder@example.test"))
    with pytest.raises(IdentityError) as error:
        await service.principal_for_user(impostor, None)
    assert error.value.code == "NO_TENANT"


async def test_bootstrap_needs_a_verified_email(service):
    unverified = await service.sync_user(ISSUER, claims("founder", email="founder@example.test", verified=False))
    with pytest.raises(IdentityError) as error:
        await service.principal_for_user(unverified, None)
    assert error.value.code == "NO_TENANT"


async def test_invitations_apply_only_to_verified_unexpired_addresses(service, database):
    founder = await service.sync_user(ISSUER, claims("founder", email="founder@example.test"))
    admin = await service.principal_for_user(founder, None)
    await service.invite(admin.tenant_id, "Bob@Example.test", "analyst", founder.id)
    bob_unverified = await service.sync_user(ISSUER, claims("bob", verified=False))
    with pytest.raises(IdentityError):
        await service.principal_for_user(bob_unverified, None)
    bob = await service.sync_user(ISSUER, claims("bob"))
    principal = await service.principal_for_user(bob, None)
    assert (principal.role, principal.email) == ("analyst", "bob@example.test")

    await service.invite(admin.tenant_id, "carol@example.test", "viewer", founder.id)
    later = IdentityService(database, clock=lambda: datetime.now(timezone.utc) + timedelta(days=15))
    carol = await later.sync_user(ISSUER, claims("carol"))
    with pytest.raises(IdentityError) as error:
        await later.principal_for_user(carol, None)
    assert error.value.code == "NO_TENANT"


async def test_tenant_selection_rules(service, database):
    service.signup_mode = "self_service"
    user = await service.sync_user(ISSUER, claims("dana"))
    first = await service.create_tenant("Blue Team", user)
    second = await service.create_tenant("Red Team", user)
    with pytest.raises(IdentityError) as error:
        await service.principal_for_user(user, None)
    assert error.value.code == "TENANT_REQUIRED"
    assert (await service.principal_for_user(user, second.slug)).tenant_id == second.id
    assert (await service.principal_for_user(user, str(first.id))).tenant_id == first.id
    with pytest.raises(IdentityError) as error:
        await service.principal_for_user(user, "someone-elses-tenant")
    assert error.value.code == "TENANT_FORBIDDEN"
    async with database.session() as session:
        (await session.get(Tenant, first.id)).status = "suspended"
    with pytest.raises(IdentityError) as error:
        await service.principal_for_user(user, first.slug)
    assert error.value.code == "TENANT_SUSPENDED"


async def test_self_service_signup_is_off_by_default_and_capped(service):
    user = await service.sync_user(ISSUER, claims("erin"))
    with pytest.raises(IdentityError) as error:
        await service.create_tenant("Mine", user)
    assert error.value.code == "SIGNUP_DISABLED"
    service.signup_mode = "self_service"
    for n in range(3):
        await service.create_tenant(f"Tenant {n}", user)
    with pytest.raises(IdentityError) as error:
        await service.create_tenant("One too many", user)
    assert error.value.code == "LIMIT_REACHED"


async def test_api_keys_authenticate_until_revoked_or_expired(service):
    founder = await service.sync_user(ISSUER, claims("founder", email="founder@example.test"))
    admin = await service.principal_for_user(founder, None)
    key, token = await service.create_api_key(admin.tenant_id, "ci", "analyst", founder.id)
    principal = await service.principal_for_api_key(token)
    assert (principal.kind, principal.id, principal.role, principal.tenant_id) == ("api_key", key.id, "analyst", admin.tenant_id)
    assert principal.can(Permission.RUN) and not principal.can(Permission.MANAGE)
    stored = (await service.list_api_keys(admin.tenant_id))[0]
    assert stored.last_used_at is not None and token.split("_")[2] not in stored.secret_sha256

    key_id, _ = apikeys.parse(token)
    for forged in (f"tsk_{key_id}_{'A' * 43}", "tsk_0123456789abcdef_" + "B" * 43, "Bearer x", ""):
        with pytest.raises(IdentityError) as error:
            await service.principal_for_api_key(forged)
        assert error.value.code == "INVALID_API_KEY"

    await service.revoke_api_key(admin.tenant_id, key.id)
    with pytest.raises(IdentityError):
        await service.principal_for_api_key(token)

    _, short_lived = await service.create_api_key(admin.tenant_id, "temp", "viewer", founder.id,
                                                  expires_at=datetime.now(timezone.utc) + timedelta(seconds=30))
    assert (await service.principal_for_api_key(short_lived)).role == "viewer"
    future = IdentityService(service.db, clock=lambda: datetime.now(timezone.utc) + timedelta(minutes=5))
    with pytest.raises(IdentityError):
        await future.principal_for_api_key(short_lived)


async def test_api_keys_never_get_admin_rights(service):
    founder = await service.sync_user(ISSUER, claims("founder", email="founder@example.test"))
    admin = await service.principal_for_user(founder, None)
    with pytest.raises(IdentityError):
        await service.create_api_key(admin.tenant_id, "root", "admin", founder.id)


async def test_api_key_of_one_tenant_cannot_be_revoked_by_another(service):
    service.signup_mode = "self_service"
    user = await service.sync_user(ISSUER, claims("frank"))
    one, two = await service.create_tenant("One", user), await service.create_tenant("Two", user)
    key, _ = await service.create_api_key(one.id, "k", "viewer", user.id)
    with pytest.raises(IdentityError) as error:
        await service.revoke_api_key(two.id, key.id)
    assert error.value.code == "NOT_FOUND"


async def test_last_admin_cannot_be_demoted_or_removed(service):
    founder = await service.sync_user(ISSUER, claims("founder", email="founder@example.test"))
    admin = await service.principal_for_user(founder, None)
    for action in (service.set_role(admin.tenant_id, founder.id, "viewer"),
                   service.remove_member(admin.tenant_id, founder.id)):
        with pytest.raises(IdentityError) as error:
            await action
        assert error.value.code == "LAST_ADMIN"
    await service.invite(admin.tenant_id, "gina@example.test", "admin", founder.id)
    gina = await service.sync_user(ISSUER, claims("gina"))
    await service.set_role(admin.tenant_id, founder.id, "viewer")
    assert (await service.principal_for_user(founder, None)).role == "viewer"
    assert (await service.principal_for_user(gina, None)).role == "admin"


async def test_local_tenant_is_stable(service):
    first, second = await service.ensure_local_tenant(), await service.ensure_local_tenant()
    assert first == second and first.role == "admin" and first.actor == "local:local"


async def test_limits_are_validated(service):
    principal = await service.ensure_local_tenant()
    tenant = await service.update_limits(principal.tenant_id, monthly_budget_usd=Decimal("12.50"), max_runs_per_day=5)
    assert (tenant.monthly_budget_usd, tenant.max_runs_per_day) == (Decimal("12.50"), 5)
    with pytest.raises(IdentityError):
        await service.update_limits(principal.tenant_id, monthly_budget_usd=Decimal("-1"), max_runs_per_day=None)


def test_permissions_by_role():
    import uuid
    def make(role):
        return Principal("user", "u", uuid.uuid4(), "t", role)
    assert [p for p in Permission if make("viewer").can(p)] == [Permission.READ]
    assert not make("analyst").can(Permission.MANAGE) and make("analyst").can(Permission.DECIDE)
    assert all(make("admin").can(p) for p in Permission)
    with pytest.raises(IdentityError) as error:
        make("viewer").require(Permission.RUN)
    assert error.value.status == 403
