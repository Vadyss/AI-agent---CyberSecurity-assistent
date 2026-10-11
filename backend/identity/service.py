# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Users, tenants, memberships, invitations and API keys.

A verified identity-provider token proves who someone is. Membership rows
in this database decide which tenants they may act in and with which role.
"""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from storage.db import Database
from storage.models import ROLES, ApiKey, Invitation, Membership, Tenant, User

from . import apikeys
from .principal import API_KEY_ROLES, IdentityError, Principal

INVITATION_TTL = timedelta(days=14)
LOGIN_TOUCH_INTERVAL = timedelta(minutes=5)
KEY_TOUCH_INTERVAL = timedelta(seconds=60)
MAX_TENANTS_CREATED_PER_USER = 3
MAX_API_KEYS_PER_TENANT = 50
LOCAL_TENANT_SLUG = "local"
# Fixed, so the local tenant's data directory survives restarts even with an ephemeral database.
LOCAL_TENANT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return slug or "tenant"


def normalize_email(value: object) -> str | None:
    if not isinstance(value, str) or "@" not in value or len(value) > 320:
        return None
    return value.strip().lower()


class IdentityService:
    def __init__(self, db: Database, *, bootstrap_admin_email: str = "", bootstrap_tenant_name: str = "Default",
                 signup_mode: str = "invite_only", clock=utcnow) -> None:
        self.db = db
        self.bootstrap_admin_email = normalize_email(bootstrap_admin_email)
        self.bootstrap_tenant_name = bootstrap_tenant_name or "Default"
        self.signup_mode = signup_mode
        self.clock = clock

    # --- Users -------------------------------------------------------------

    async def sync_user(self, issuer: str, claims: dict) -> User:
        """Create or refresh the user behind a verified token, then apply invitations."""
        for attempt in range(2):
            try:
                return await self._sync_user(issuer, claims)
            except IntegrityError:
                if attempt:  # A concurrent first login created the same user; read it again.
                    raise
        raise AssertionError("unreachable")

    async def _sync_user(self, issuer: str, claims: dict) -> User:
        now = self.clock()
        email = normalize_email(claims.get("email"))
        verified = claims.get("email_verified") is True and email is not None
        name = claims.get("name") or claims.get("preferred_username")
        name = name[:200] if isinstance(name, str) else None
        async with self.db.session() as session:
            user = (await session.execute(sa.select(User).where(
                User.issuer == issuer, User.subject == claims["sub"]))).scalar_one_or_none()
            if user is None:
                user = User(issuer=issuer, subject=claims["sub"], email=email, email_verified=verified,
                            display_name=name, last_login_at=now)
                session.add(user)
                await session.flush()
            else:
                if (user.email, user.email_verified, user.display_name) != (email, verified, name):
                    user.email, user.email_verified, user.display_name = email, verified, name
                if user.last_login_at is None or now - user.last_login_at > LOGIN_TOUCH_INTERVAL:
                    user.last_login_at = now
            if verified:
                await self._accept_invitations(session, user, now)
                await self._bootstrap_admin(session, user)
            return user

    async def _accept_invitations(self, session, user: User, now: datetime) -> None:
        invitations = (await session.execute(sa.select(Invitation).where(
            Invitation.email == user.email, Invitation.accepted_at.is_(None),
            Invitation.expires_at > now))).scalars().all()
        for invitation in invitations:
            existing = await session.get(Membership, (invitation.tenant_id, user.id))
            if existing is None:
                session.add(Membership(tenant_id=invitation.tenant_id, user_id=user.id, role=invitation.role))
            invitation.accepted_at = now

    async def _bootstrap_admin(self, session, user: User) -> None:
        """The configured first administrator gets the first tenant, exactly once."""
        if not self.bootstrap_admin_email or user.email != self.bootstrap_admin_email:
            return
        slug = slugify(self.bootstrap_tenant_name)
        if (await session.execute(sa.select(Tenant.id).where(Tenant.slug == slug))).first() is not None:
            return
        tenant = Tenant(slug=slug, name=self.bootstrap_tenant_name[:200])
        session.add(tenant)
        await session.flush()
        session.add(Membership(tenant_id=tenant.id, user_id=user.id, role="admin"))

    async def memberships(self, user_id: uuid.UUID) -> list[tuple[Tenant, str]]:
        async with self.db.session() as session:
            rows = (await session.execute(
                sa.select(Tenant, Membership.role).join(Membership, Membership.tenant_id == Tenant.id)
                .where(Membership.user_id == user_id).order_by(Tenant.name))).all()
            return [(tenant, role) for tenant, role in rows]

    async def principal_for_user(self, user: User, requested_tenant: str | None) -> Principal:
        memberships = await self.memberships(user.id)
        if requested_tenant:
            chosen = [(t, r) for t, r in memberships if requested_tenant in (str(t.id), t.slug)]
            if not chosen:
                # Same answer whether the tenant exists or not.
                raise IdentityError(403, "TENANT_FORBIDDEN", "You are not a member of this tenant.")
        elif not memberships:
            hint = " or create your own tenant" if self.signup_mode == "self_service" else ""
            raise IdentityError(403, "NO_TENANT", f"Your account is not a member of any tenant yet. "
                                                  f"Ask an administrator for an invitation{hint}.")
        elif len(memberships) > 1:
            raise IdentityError(400, "TENANT_REQUIRED", "Choose a tenant with the X-Toolsmith-Tenant header.")
        else:
            chosen = memberships
        tenant, role = chosen[0]
        self._require_active(tenant)
        return Principal("user", str(user.id), tenant.id, tenant.slug, role, user.email)

    # --- API keys ----------------------------------------------------------

    async def principal_for_api_key(self, token: str) -> Principal:
        refused = IdentityError(401, "INVALID_API_KEY", "The API key is invalid, expired or revoked.")
        parsed = apikeys.parse(token)
        if parsed is None:
            raise refused
        key_id, secret = parsed
        now = self.clock()
        async with self.db.session() as session:
            key = await session.get(ApiKey, key_id)
            # Compare the secret even for unknown keys, so timing does not reveal valid IDs.
            valid = apikeys.matches(secret, key.secret_sha256 if key else "0" * 64)
            if key is None or not valid or key.revoked_at is not None or (key.expires_at and key.expires_at <= now):
                raise refused
            tenant = self._require_active(await session.get(Tenant, key.tenant_id))
            if key.last_used_at is None or now - key.last_used_at > KEY_TOUCH_INTERVAL:
                key.last_used_at = now
            return Principal("api_key", key.id, tenant.id, tenant.slug, key.role)

    async def create_api_key(self, tenant_id: uuid.UUID, name: str, role: str, created_by: uuid.UUID | None,
                             expires_at: datetime | None = None) -> tuple[ApiKey, str]:
        if role not in API_KEY_ROLES:
            raise IdentityError(400, "INVALID_REQUEST", "API keys can only have the analyst or viewer role.")
        if expires_at is not None and expires_at <= self.clock():
            raise IdentityError(400, "INVALID_REQUEST", "The expiry must be in the future.")
        key_id, secret_digest, token = apikeys.generate()
        async with self.db.session() as session:
            active = (await session.execute(sa.select(sa.func.count()).select_from(ApiKey).where(
                ApiKey.tenant_id == tenant_id, ApiKey.revoked_at.is_(None)))).scalar_one()
            if active >= MAX_API_KEYS_PER_TENANT:
                raise IdentityError(409, "LIMIT_REACHED", "This tenant has reached its API key limit.")
            key = ApiKey(id=key_id, tenant_id=tenant_id, name=name[:100], role=role, secret_sha256=secret_digest,
                         created_by=created_by, expires_at=expires_at)
            session.add(key)
        return key, token

    async def list_api_keys(self, tenant_id: uuid.UUID) -> list[ApiKey]:
        async with self.db.session() as session:
            return list((await session.execute(sa.select(ApiKey).where(ApiKey.tenant_id == tenant_id)
                                               .order_by(ApiKey.created_at))).scalars().all())

    async def revoke_api_key(self, tenant_id: uuid.UUID, key_id: str) -> None:
        async with self.db.session() as session:
            key = await session.get(ApiKey, key_id)
            if key is None or key.tenant_id != tenant_id:
                raise IdentityError(404, "NOT_FOUND", "API key not found.")
            key.revoked_at = key.revoked_at or self.clock()

    # --- Tenants and members -------------------------------------------------

    async def ensure_local_tenant(self) -> Principal:
        """The single tenant used when authentication is disabled (development and tests)."""
        async with self.db.session() as session:
            tenant = await session.get(Tenant, LOCAL_TENANT_ID)
            if tenant is None:
                tenant = Tenant(id=LOCAL_TENANT_ID, slug=LOCAL_TENANT_SLUG, name="Local")
                session.add(tenant)
                await session.flush()
            return Principal("local", "local", tenant.id, tenant.slug, "admin")

    async def create_tenant(self, name: str, user: User) -> Tenant:
        if self.signup_mode != "self_service":
            raise IdentityError(403, "SIGNUP_DISABLED", "New tenants are created by the operator on invitation.")
        name = name.strip()
        if not 2 <= len(name) <= 200:
            raise IdentityError(400, "INVALID_REQUEST", "The tenant name must have 2 to 200 characters.")
        async with self.db.session() as session:
            owned = (await session.execute(sa.select(sa.func.count()).select_from(Membership).where(
                Membership.user_id == user.id, Membership.role == "admin"))).scalar_one()
            if owned >= MAX_TENANTS_CREATED_PER_USER:
                raise IdentityError(409, "LIMIT_REACHED", "You already administer the maximum number of tenants.")
            tenant = Tenant(slug=f"{slugify(name)}-{secrets.token_hex(3)}", name=name)
            session.add(tenant)
            await session.flush()
            session.add(Membership(tenant_id=tenant.id, user_id=user.id, role="admin"))
            return tenant

    async def get_tenant(self, tenant_id: uuid.UUID) -> Tenant:
        async with self.db.session() as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                raise IdentityError(404, "NOT_FOUND", "Tenant not found.")
            return tenant

    async def update_limits(self, tenant_id: uuid.UUID, *, monthly_budget_usd: Decimal | None,
                            max_runs_per_day: int | None) -> Tenant:
        if monthly_budget_usd is not None and (not monthly_budget_usd.is_finite() or monthly_budget_usd < 0):
            raise IdentityError(400, "INVALID_REQUEST", "The budget must not be negative.")
        if max_runs_per_day is not None and max_runs_per_day < 0:
            raise IdentityError(400, "INVALID_REQUEST", "The daily run limit must not be negative.")
        async with self.db.session() as session:
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                raise IdentityError(404, "NOT_FOUND", "Tenant not found.")
            tenant.monthly_budget_usd, tenant.max_runs_per_day = monthly_budget_usd, max_runs_per_day
            return tenant

    async def list_members(self, tenant_id: uuid.UUID) -> list[tuple[User, str]]:
        async with self.db.session() as session:
            rows = (await session.execute(
                sa.select(User, Membership.role).join(Membership, Membership.user_id == User.id)
                .where(Membership.tenant_id == tenant_id).order_by(User.email))).all()
            return [(user, role) for user, role in rows]

    async def set_role(self, tenant_id: uuid.UUID, user_id: uuid.UUID, role: str) -> None:
        if role not in ROLES:
            raise IdentityError(400, "INVALID_REQUEST", "Unknown role.")
        async with self.db.session() as session:
            membership = await self._membership(session, tenant_id, user_id)
            if membership.role == "admin" and role != "admin":
                await self._keep_an_admin(session, tenant_id)
            membership.role = role

    async def remove_member(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
        async with self.db.session() as session:
            membership = await self._membership(session, tenant_id, user_id)
            if membership.role == "admin":
                await self._keep_an_admin(session, tenant_id)
            await session.delete(membership)

    async def invite(self, tenant_id: uuid.UUID, email: str, role: str, invited_by: uuid.UUID | None) -> Invitation:
        address = normalize_email(email)
        if address is None:
            raise IdentityError(400, "INVALID_REQUEST", "A valid e-mail address is required.")
        if role not in ROLES:
            raise IdentityError(400, "INVALID_REQUEST", "Unknown role.")
        now = self.clock()
        async with self.db.session() as session:
            invitation = (await session.execute(sa.select(Invitation).where(
                Invitation.tenant_id == tenant_id, Invitation.email == address))).scalar_one_or_none()
            if invitation is None:
                invitation = Invitation(tenant_id=tenant_id, email=address, role=role, invited_by=invited_by,
                                        expires_at=now + INVITATION_TTL)
                session.add(invitation)
            else:
                invitation.role, invitation.invited_by = role, invited_by
                invitation.expires_at, invitation.accepted_at = now + INVITATION_TTL, None
            await session.flush()
            return invitation

    async def list_invitations(self, tenant_id: uuid.UUID) -> list[Invitation]:
        async with self.db.session() as session:
            return list((await session.execute(sa.select(Invitation).where(Invitation.tenant_id == tenant_id)
                                               .order_by(Invitation.created_at))).scalars().all())

    async def revoke_invitation(self, tenant_id: uuid.UUID, invitation_id: uuid.UUID) -> None:
        async with self.db.session() as session:
            invitation = await session.get(Invitation, invitation_id)
            if invitation is None or invitation.tenant_id != tenant_id:
                raise IdentityError(404, "NOT_FOUND", "Invitation not found.")
            await session.delete(invitation)

    # --- Helpers -------------------------------------------------------------

    @staticmethod
    def _require_active(tenant: Tenant | None) -> Tenant:
        if tenant is None:
            raise IdentityError(403, "TENANT_FORBIDDEN", "You are not a member of this tenant.")
        if tenant.status != "active":
            raise IdentityError(403, "TENANT_SUSPENDED", "This tenant is suspended.")
        return tenant

    @staticmethod
    async def _membership(session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
        membership = await session.get(Membership, (tenant_id, user_id))
        if membership is None:
            raise IdentityError(404, "NOT_FOUND", "Member not found.")
        return membership

    @staticmethod
    async def _keep_an_admin(session, tenant_id: uuid.UUID) -> None:
        admins = (await session.execute(sa.select(sa.func.count()).select_from(Membership).where(
            Membership.tenant_id == tenant_id, Membership.role == "admin"))).scalar_one()
        if admins <= 1:
            raise IdentityError(409, "LAST_ADMIN", "A tenant must keep at least one administrator.")
