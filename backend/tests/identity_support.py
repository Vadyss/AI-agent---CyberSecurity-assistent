# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""A local identity provider for tests: real RSA keys, a JWKS endpoint, signed tokens."""
from __future__ import annotations

import base64
import json
import time
import uuid

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.test"
AUDIENCE = "toolsmith-proxy"
JWKS_URL = "http://idp.internal/jwks"


class TestIdp:
    __test__ = False  # Not a pytest test class.

    def __init__(self) -> None:
        self.keys: dict[str, rsa.RSAPrivateKey] = {}
        self.published: list[str] = []
        self.fetches = 0
        self.down = False
        self.rotate()

    def rotate(self) -> str:
        kid = uuid.uuid4().hex[:8]
        self.keys[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.published.append(kid)
        self.kid = kid
        return kid

    def jwks(self) -> dict:
        keys = []
        for kid in self.published:
            jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.keys[kid].public_key(), as_dict=True)
            keys.append({**jwk, "kid": kid, "use": "sig", "alg": "RS256"})
        return {"keys": keys}

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.fetches += 1
            if self.down or str(request.url) != JWKS_URL:
                return httpx.Response(503)
            return httpx.Response(200, json=self.jwks())
        return httpx.MockTransport(handler)

    def token(self, subject: str = "user-1", *, kid: str | None = None, algorithm: str = "RS256",
              headers: dict | None = None, **claims) -> str:
        now = int(time.time())
        payload = {"iss": ISSUER, "aud": AUDIENCE, "sub": subject, "iat": now, "exp": now + 300,
                   "email": f"{subject}@example.test", "email_verified": True, **claims}
        payload = {key: value for key, value in payload.items() if value is not None}
        kid = kid or self.kid
        return jwt.encode(payload, self.keys[kid], algorithm=algorithm, headers={"kid": kid, **(headers or {})})

    def public_pem(self) -> bytes:
        return self.keys[self.kid].public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def unsigned_token(**claims) -> str:
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    now = int(time.time())
    body = {"iss": ISSUER, "aud": AUDIENCE, "sub": "attacker", "iat": now, "exp": now + 300, **claims}
    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(body)}."


def bearer(idp: TestIdp, subject: str, email: str | None = None, **claims) -> dict:
    return {"Authorization": "Bearer " + idp.token(subject, email=email or f"{subject}@example.test", **claims)}


def call(client, function, *args, **kwargs):
    """Run an async service call on the TestClient's event loop."""
    return client.portal.call(lambda: function(*args, **kwargs))


def wait_finished(client, run_id: str, headers: dict, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runs = {r["run_id"]: r for r in client.get("/runs", headers=headers).json()["runs"]}
        if runs[run_id]["status"] not in {"queued", "running"}:
            return runs[run_id]
        time.sleep(0.02)
    raise AssertionError("The run did not finish.")
