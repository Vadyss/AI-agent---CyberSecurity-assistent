# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Request authentication and permission checks for HTTP and WebSocket.

Accepted credentials, in order:
1. ``X-API-Key: tsk_...`` for programmatic clients;
2. ``Authorization: Bearer <ID token>`` injected by oauth2-proxy and verified here;
3. nothing at all, only when AUTH_MODE=disabled (one local tenant, development only).
"""
from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Mapping

from fastapi import Request

from gatekeeper.api import AUDIT_ACTOR
from identity.oidc import OidcVerifier, ProviderUnavailable, TokenError
from identity.principal import IdentityError, Permission, Principal
from identity.service import IdentityService

TENANT_HEADER = "x-toolsmith-tenant"
PRINCIPAL_CACHE_S = 60
PRINCIPAL_CACHE_SIZE = 10_000


class Authenticator:
    def __init__(self, identity: IdentityService, *, issuer: str = "", verifier: OidcVerifier | None = None,
                 local: Principal | None = None, clock=time.monotonic) -> None:
        self.identity = identity
        self.issuer = issuer
        self.verifier = verifier
        self.local = local
        self.clock = clock
        # Verified token -> principal for a short time, so each request does not
        # write the user row. Membership changes apply within PRINCIPAL_CACHE_S.
        self._cache: OrderedDict[str, tuple[float, Principal]] = OrderedDict()

    async def authenticate(self, headers: Mapping[str, str], query_tenant: str | None = None) -> Principal:
        if self.local is not None:
            return self.local
        api_key = headers.get("x-api-key")
        if api_key:
            return await self.identity.principal_for_api_key(api_key.strip())
        scheme, _, token = headers.get("authorization", "").partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token or self.verifier is None:
            raise IdentityError(401, "AUTHENTICATION_REQUIRED", "Sign in to continue.")
        tenant = headers.get(TENANT_HEADER) or query_tenant or None
        key = hashlib.sha256(f"{tenant}\x00{token}".encode()).hexdigest()
        cached = self._cache.get(key)
        if cached and cached[0] > self.clock():
            self._cache.move_to_end(key)
            return cached[1]
        try:
            claims = await self.verifier.verify(token)
        except ProviderUnavailable:
            raise IdentityError(503, "IDENTITY_PROVIDER_UNAVAILABLE",
                                "The identity provider is unavailable; try again shortly.") from None
        except TokenError as error:
            raise IdentityError(401, "INVALID_TOKEN", str(error)) from None
        user = await self.identity.sync_user(self.issuer, claims)
        principal = await self.identity.principal_for_user(user, tenant)
        lifetime = min(PRINCIPAL_CACHE_S, max(0, claims["exp"] - time.time()))
        self._cache[key] = (self.clock() + lifetime, principal)
        while len(self._cache) > PRINCIPAL_CACHE_SIZE:
            self._cache.popitem(last=False)
        return principal

    async def user(self, headers: Mapping[str, str]):
        """The signed-in user without choosing a tenant (onboarding)."""
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip() or self.verifier is None:
            raise IdentityError(401, "AUTHENTICATION_REQUIRED", "Sign in with your identity provider to continue.")
        try:
            claims = await self.verifier.verify(token.strip())
        except ProviderUnavailable:
            raise IdentityError(503, "IDENTITY_PROVIDER_UNAVAILABLE",
                                "The identity provider is unavailable; try again shortly.") from None
        except TokenError as error:
            raise IdentityError(401, "INVALID_TOKEN", str(error)) from None
        return await self.identity.sync_user(self.issuer, claims)

    def forget(self) -> None:
        """Drop cached principals, for example after a role change."""
        self._cache.clear()


def requires(permission: Permission):
    """FastAPI dependency: authenticate, check the permission, tag the audit actor."""
    async def dependency(request: Request) -> Principal:
        principal = await request.app.state.auth.authenticate(request.headers)
        principal.require(permission)
        request.state.principal = principal
        AUDIT_ACTOR.set(principal.actor)
        return principal
    return dependency
