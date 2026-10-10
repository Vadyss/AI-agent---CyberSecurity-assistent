# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Verification of identity-provider tokens forwarded by oauth2-proxy.

The backend never trusts identity headers such as X-Forwarded-User. It
accepts a user only from a JWT whose signature verifies against the
provider's published keys and whose issuer and audience match. A request
that bypassed the proxy therefore has no identity at all.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import jwt

MAX_TOKEN_BYTES = 16384
ASYMMETRIC_KEY_TYPES = {"RSA", "EC", "OKP"}


class TokenError(Exception):
    """A client-safe reason why a token was refused."""


class ProviderUnavailable(TokenError):
    """The provider's keys could not be loaded and none are cached."""


class JwksCache:
    def __init__(self, url: str, http: httpx.AsyncClient, *, ttl_s: float = 600,
                 min_refresh_interval_s: float = 30, clock=time.monotonic) -> None:
        self.url = url
        self.http = http
        self.ttl_s = ttl_s
        self.min_refresh_interval_s = min_refresh_interval_s
        self.clock = clock
        self._keys: dict[str | None, jwt.PyJWK] | None = None
        self._fetched_at = float("-inf")
        self._attempted_at = float("-inf")
        self._lock = asyncio.Lock()

    async def key_for(self, kid: str | None) -> jwt.PyJWK:
        if self._keys is None or self.clock() - self._fetched_at > self.ttl_s:
            await self._refresh()
        key = self._lookup(kid)
        # An unknown key id may mean the provider rotated keys. Refetch, but at
        # most once per interval, so forged key ids cannot flood the provider.
        if key is None and self.clock() - self._attempted_at >= self.min_refresh_interval_s:
            await self._refresh()
            key = self._lookup(kid)
        if key is None:
            raise TokenError("The token was signed with an unknown key.")
        return key

    def _lookup(self, kid: str | None) -> jwt.PyJWK | None:
        keys = self._keys or {}
        if kid is None and len(keys) == 1:
            return next(iter(keys.values()))
        return keys.get(kid)

    async def _refresh(self) -> None:
        async with self._lock:
            self._attempted_at = self.clock()
            try:
                response = await self.http.get(self.url, timeout=5)
                response.raise_for_status()
                key_set = jwt.PyJWKSet.from_dict(response.json())
            except (httpx.HTTPError, ValueError, jwt.PyJWKSetError, jwt.InvalidKeyError):
                if self._keys is None:
                    raise ProviderUnavailable("The identity provider keys are unavailable.") from None
                return  # Keep serving the cached keys while the provider recovers.
            keys = {key.key_id: key for key in key_set.keys
                    if key.key_type in ASYMMETRIC_KEY_TYPES and key.public_key_use in (None, "sig")}
            if not keys:
                if self._keys is None:
                    raise ProviderUnavailable("The identity provider publishes no usable signing keys.")
                return
            self._keys, self._fetched_at = keys, self.clock()


class OidcVerifier:
    def __init__(self, issuer: str, audience: str, jwks: JwksCache, *,
                 algorithms: tuple[str, ...] = ("RS256", "ES256"), leeway_s: int = 30) -> None:
        self.issuer = issuer
        self.audience = audience
        self.jwks = jwks
        self.algorithms = tuple(algorithms)
        self.leeway_s = leeway_s

    async def verify(self, token: str) -> dict[str, Any]:
        if not token or len(token.encode("utf-8", "replace")) > MAX_TOKEN_BYTES:
            raise TokenError("The token is missing or too large.")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise TokenError("The token is malformed.") from None
        algorithm = header.get("alg")
        # Fixing the algorithm list here blocks "none" and HMAC key-confusion tokens.
        if algorithm not in self.algorithms:
            raise TokenError("The token algorithm is not accepted.")
        kid = header.get("kid")
        key = await self.jwks.key_for(kid if isinstance(kid, str) else None)
        if key.algorithm_name and key.algorithm_name != algorithm:
            raise TokenError("The token algorithm does not match its key.")
        try:
            claims = jwt.decode(token, key.key, algorithms=[algorithm], audience=self.audience,
                                issuer=self.issuer, leeway=self.leeway_s,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except jwt.ExpiredSignatureError:
            raise TokenError("The token has expired.") from None
        except jwt.InvalidTokenError:
            raise TokenError("The token is invalid.") from None
        audience = claims["aud"]
        # OIDC Core 3.1.3.7: with several audiences, the authorized party must be us.
        if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != self.audience:
            raise TokenError("The token was issued to another client.")
        subject = claims["sub"]
        if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
            raise TokenError("The token subject is invalid.")
        return claims
