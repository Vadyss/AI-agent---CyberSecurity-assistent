# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Who is calling, in which tenant, and what they may do."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Literal


class Permission(str, Enum):
    READ = "read"                # runs, events, skills, usage
    RUN = "run"                  # start, cancel and retry runs
    DECIDE = "decide"            # approve or reject a rule and its new tools
    REVIEW_CODE = "review_code"  # read candidate tool code before deciding
    MANAGE = "manage"            # members, invitations, API keys, quotas, audit export, deletion


ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "viewer": frozenset({Permission.READ}),
    "analyst": frozenset({Permission.READ, Permission.RUN, Permission.DECIDE, Permission.REVIEW_CODE}),
    "admin": frozenset(Permission),
}
API_KEY_ROLES = ("analyst", "viewer")


class IdentityError(Exception):
    """A client-safe authentication or authorization failure."""

    retry_after: int | None = None  # Seconds, for 429 answers.

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "api_key", "local"]
    id: str
    tenant_id: uuid.UUID
    tenant_slug: str
    role: str
    email: str | None = None

    @property
    def actor(self) -> str:
        """Stable label for audit records and run ownership."""
        return f"{self.kind}:{self.id}"

    def can(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS.get(self.role, frozenset())

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise IdentityError(403, "FORBIDDEN", "Your role does not allow this action.")
