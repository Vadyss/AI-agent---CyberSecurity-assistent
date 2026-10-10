# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""API key format: ``tsk_<16 hex id>_<43 char secret>``.

Only the SHA-256 of the secret is stored. The secret has 256 bits of
entropy, so a fast hash is enough; a slow password hash protects low-entropy
human passwords, which these are not.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

PATTERN = re.compile(r"^tsk_([0-9a-f]{16})_([A-Za-z0-9_-]{43})$")


def generate() -> tuple[str, str, str]:
    """Return (key id, secret digest, full token). The token is shown once."""
    key_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    return key_id, digest(secret), f"tsk_{key_id}_{secret}"


def parse(token: str) -> tuple[str, str] | None:
    match = PATTERN.fullmatch(token or "")
    return (match.group(1), match.group(2)) if match else None


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def matches(secret: str, stored_digest: str) -> bool:
    return hmac.compare_digest(digest(secret), stored_digest)
