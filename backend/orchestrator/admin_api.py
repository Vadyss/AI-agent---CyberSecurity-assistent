# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Tenant administration and onboarding endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from identity.principal import IdentityError, Permission, Principal
from orchestrator.auth import requires


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LimitsBody(Body):
    monthly_budget_usd: Decimal | None = Field(default=None, ge=0, le=1_000_000, max_digits=18, decimal_places=6)
    max_runs_per_day: int | None = Field(default=None, ge=0, le=100_000)


class RoleBody(Body):
    role: Literal["admin", "analyst", "viewer"]


class InvitationBody(Body):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["admin", "analyst", "viewer"]


class ApiKeyBody(Body):
    name: str = Field(min_length=1, max_length=100)
    role: Literal["analyst", "viewer"]
    expires_in_days: int | None = Field(default=None, ge=1, le=730)


class TenantBody(Body):
    name: str = Field(min_length=2, max_length=200)


def iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def money(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def parse_uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise IdentityError(404, "NOT_FOUND", f"{what} not found.") from None


def build_admin_router(settings) -> APIRouter:
    router = APIRouter()
    reader = Depends(requires(Permission.READ))
    manager = Depends(requires(Permission.MANAGE))

    def service(request: Request):
        return request.app.state.identity

    @router.get("/tenant")
    async def tenant(request: Request, principal: Principal = reader):
        tenant = await service(request).get_tenant(principal.tenant_id)
        return {"id": str(tenant.id), "slug": tenant.slug, "name": tenant.name, "status": tenant.status,
                "created_at": iso(tenant.created_at),
                "limits": {"monthly_budget_usd": money(tenant.monthly_budget_usd) or settings.default_monthly_budget_usd,
                           "max_runs_per_day": tenant.max_runs_per_day if tenant.max_runs_per_day is not None
                           else settings.default_max_runs_per_day,
                           "custom": tenant.monthly_budget_usd is not None or tenant.max_runs_per_day is not None}}

    @router.patch("/tenant/limits")
    async def update_limits(body: LimitsBody, request: Request, principal: Principal = manager):
        tenant = await service(request).update_limits(principal.tenant_id, monthly_budget_usd=body.monthly_budget_usd,
                                                      max_runs_per_day=body.max_runs_per_day)
        return {"monthly_budget_usd": money(tenant.monthly_budget_usd), "max_runs_per_day": tenant.max_runs_per_day}

    @router.get("/members")
    async def members(request: Request, principal: Principal = manager):
        rows = await service(request).list_members(principal.tenant_id)
        return {"members": [{"user_id": str(user.id), "email": user.email, "display_name": user.display_name,
                             "role": role, "last_login_at": iso(user.last_login_at)} for user, role in rows]}

    @router.patch("/members/{user_id}")
    async def set_role(user_id: str, body: RoleBody, request: Request, principal: Principal = manager):
        await service(request).set_role(principal.tenant_id, parse_uuid(user_id, "Member"), body.role)
        request.app.state.auth.forget()
        return {"user_id": user_id, "role": body.role}

    @router.delete("/members/{user_id}", status_code=204)
    async def remove_member(user_id: str, request: Request, principal: Principal = manager):
        await service(request).remove_member(principal.tenant_id, parse_uuid(user_id, "Member"))
        request.app.state.auth.forget()

    @router.get("/invitations")
    async def invitations(request: Request, principal: Principal = manager):
        rows = await service(request).list_invitations(principal.tenant_id)
        return {"invitations": [{"id": str(i.id), "email": i.email, "role": i.role, "created_at": iso(i.created_at),
                                 "expires_at": iso(i.expires_at), "accepted_at": iso(i.accepted_at)} for i in rows]}

    @router.post("/invitations", status_code=201)
    async def invite(body: InvitationBody, request: Request, principal: Principal = manager):
        invited_by = uuid.UUID(principal.id) if principal.kind == "user" else None
        invitation = await service(request).invite(principal.tenant_id, body.email, body.role, invited_by)
        return {"id": str(invitation.id), "email": invitation.email, "role": invitation.role,
                "expires_at": iso(invitation.expires_at)}

    @router.delete("/invitations/{invitation_id}", status_code=204)
    async def revoke_invitation(invitation_id: str, request: Request, principal: Principal = manager):
        await service(request).revoke_invitation(principal.tenant_id, parse_uuid(invitation_id, "Invitation"))

    @router.get("/api-keys")
    async def api_keys(request: Request, principal: Principal = manager):
        keys = await service(request).list_api_keys(principal.tenant_id)
        return {"api_keys": [{"id": k.id, "name": k.name, "role": k.role, "created_at": iso(k.created_at),
                              "expires_at": iso(k.expires_at), "last_used_at": iso(k.last_used_at),
                              "revoked_at": iso(k.revoked_at)} for k in keys]}

    @router.post("/api-keys", status_code=201)
    async def create_api_key(body: ApiKeyBody, request: Request, principal: Principal = manager):
        expires_at = (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
                      if body.expires_in_days else None)
        created_by = uuid.UUID(principal.id) if principal.kind == "user" else None
        key, token = await service(request).create_api_key(principal.tenant_id, body.name, body.role,
                                                           created_by, expires_at)
        # The only time the secret is ever shown.
        return {"id": key.id, "name": key.name, "role": key.role, "expires_at": iso(key.expires_at), "key": token}

    @router.delete("/api-keys/{key_id}", status_code=204)
    async def revoke_api_key(key_id: str, request: Request, principal: Principal = manager):
        await service(request).revoke_api_key(principal.tenant_id, key_id)

    # Onboarding works before the caller belongs to any tenant, so it checks the
    # signed-in user directly instead of resolving a tenant principal.
    @router.get("/onboarding")
    async def onboarding(request: Request):
        auth = request.app.state.auth
        if auth.local is not None:
            return {"memberships": [{"tenant": {"id": str(auth.local.tenant_id), "slug": auth.local.tenant_slug},
                                     "role": "admin"}], "can_create_tenant": False}
        user = await auth.user(request.headers)
        rows = await service(request).memberships(user.id)
        return {"email": user.email, "email_verified": user.email_verified,
                "memberships": [{"tenant": {"id": str(t.id), "slug": t.slug, "name": t.name}, "role": role}
                                for t, role in rows],
                "can_create_tenant": settings.signup_mode == "self_service"}

    @router.post("/onboarding/tenants", status_code=201)
    async def create_tenant(body: TenantBody, request: Request):
        auth = request.app.state.auth
        if auth.local is not None:
            raise IdentityError(403, "SIGNUP_DISABLED", "Tenants cannot be created while authentication is disabled.")
        user = await auth.user(request.headers)
        if not user.email_verified:
            raise IdentityError(403, "EMAIL_NOT_VERIFIED", "Verify your e-mail address with your identity provider first.")
        tenant = await service(request).create_tenant(body.name, user)
        auth.forget()
        return {"id": str(tenant.id), "slug": tenant.slug, "name": tenant.name, "role": "admin"}

    return router
