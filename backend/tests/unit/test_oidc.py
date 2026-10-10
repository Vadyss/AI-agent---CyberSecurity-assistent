# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx
import jwt
import pytest

from identity.oidc import JwksCache, OidcVerifier, ProviderUnavailable, TokenError
from tests.identity_support import AUDIENCE, ISSUER, JWKS_URL, TestIdp, unsigned_token


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def idp():
    return TestIdp()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def verifier(idp, clock):
    http = httpx.AsyncClient(transport=idp.transport())
    yield OidcVerifier(ISSUER, AUDIENCE, JwksCache(JWKS_URL, http, clock=clock))
    await http.aclose()


async def test_valid_token_yields_claims(verifier, idp):
    claims = await verifier.verify(idp.token("alice", name="Alice"))
    assert (claims["sub"], claims["email"], claims["name"]) == ("alice", "alice@example.test", "Alice")


@pytest.mark.parametrize("change", [
    {"iss": "https://evil.test"},
    {"aud": "another-client"},
    {"exp": int(time.time()) - 3600},
    {"sub": None},
    {"sub": ""},
    {"aud": [AUDIENCE, "other"]},  # several audiences without azp
    {"aud": [AUDIENCE, "other"], "azp": "other"},
])
async def test_wrong_claims_are_refused(verifier, idp, change):
    with pytest.raises(TokenError):
        await verifier.verify(idp.token("mallory", **change))


async def test_several_audiences_with_our_azp_are_accepted(verifier, idp):
    claims = await verifier.verify(idp.token("bob", aud=[AUDIENCE, "other"], azp=AUDIENCE))
    assert claims["sub"] == "bob"


async def test_unsigned_token_is_refused(verifier):
    with pytest.raises(TokenError, match="algorithm"):
        await verifier.verify(unsigned_token())


async def test_hmac_key_confusion_is_refused(verifier, idp):
    # The classic attack: sign with HS256 using the provider's public key as the
    # secret. PyJWT refuses to create such a token, so it is assembled by hand.
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    signing_input = (part({"alg": "HS256", "typ": "JWT", "kid": idp.kid}) + "." +
                     part({"iss": ISSUER, "aud": AUDIENCE, "sub": "root", "iat": int(time.time()),
                           "exp": int(time.time()) + 300}))
    signature = hmac.new(idp.public_pem(), signing_input.encode(), hashlib.sha256).digest()
    forged = signing_input + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    with pytest.raises(TokenError, match="algorithm"):
        await verifier.verify(forged)


async def test_tampered_payload_is_refused(verifier, idp):
    header, _, signature = idp.token("alice").split(".")
    other = idp.token("admin").split(".")[1]
    with pytest.raises(TokenError):
        await verifier.verify(f"{header}.{other}.{signature}")


async def test_token_signed_by_an_unpublished_key_is_refused(verifier, idp):
    rogue = TestIdp()
    with pytest.raises(TokenError, match="unknown key"):
        await verifier.verify(rogue.token("alice", kid=rogue.kid))


async def test_unknown_key_ids_cannot_flood_the_provider(verifier, idp, clock):
    await verifier.verify(idp.token())
    fetches = idp.fetches
    rogue = TestIdp()
    for _ in range(20):
        with pytest.raises(TokenError):
            await verifier.verify(rogue.token(kid=rogue.kid))
    assert idp.fetches == fetches  # The keys were just fetched; no refetch yet.
    clock.now += 31
    for _ in range(20):
        with pytest.raises(TokenError):
            await verifier.verify(rogue.token(kid=rogue.kid))
    assert idp.fetches == fetches + 1  # At most one refetch per interval.


async def test_key_rotation_is_picked_up(verifier, idp, clock):
    await verifier.verify(idp.token())
    clock.now += 31  # Past the minimum refresh interval.
    idp.rotate()
    assert (await verifier.verify(idp.token("carol")))["sub"] == "carol"


async def test_cached_keys_survive_a_provider_outage(verifier, idp, clock):
    await verifier.verify(idp.token())
    idp.down = True
    clock.now += 3600  # Cache expired, refresh fails, cached keys stay usable.
    assert (await verifier.verify(idp.token("dave")))["sub"] == "dave"


async def test_provider_outage_without_cache_is_reported(verifier, idp):
    idp.down = True
    with pytest.raises(ProviderUnavailable):
        await verifier.verify(idp.token())


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a" * 20000])
async def test_malformed_tokens_are_refused(verifier, token):
    with pytest.raises(TokenError):
        await verifier.verify(token)
