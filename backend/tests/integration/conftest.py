# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Shared API fixtures: a real app with OIDC against a local test identity provider."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from identity.service import IdentityService
from orchestrator.main import create_app
from tests.fakes import InProcessSandbox, make_settings
from tests.identity_support import AUDIENCE, ISSUER, JWKS_URL, TestIdp, bearer, call

FOUNDER = "founder@example.test"


@pytest.fixture
def idp():
    return TestIdp()


@pytest.fixture
def app_client(tmp_path, idp):
    settings = make_settings(data_dir=tmp_path / "data", auth_mode="oidc", oidc_issuer=ISSUER,
                             oidc_audience=AUDIENCE, oidc_jwks_url=JWKS_URL, bootstrap_admin_email=FOUNDER,
                             bootstrap_tenant_name="Acme", signup_mode="self_service")
    with TestClient(create_app(settings=settings, sandbox=InProcessSandbox(), oidc_transport=idp.transport())) as client:
        yield client


@pytest.fixture
def world(app_client, idp):
    """Tenant Acme (founder=admin, alice=analyst, victor=viewer) and tenant Globex (mallory=admin)."""
    service: IdentityService = app_client.app.state.identity
    founder = bearer(idp, "founder", FOUNDER)
    acme = app_client.get("/me", headers=founder).json()["tenant"]
    founder_user = call(app_client, service.sync_user, ISSUER, {"sub": "founder", "email": FOUNDER, "email_verified": True})
    acme_id = uuid.UUID(acme["id"])
    call(app_client, service.invite, acme_id, "alice@example.test", "analyst", founder_user.id)
    call(app_client, service.invite, acme_id, "victor@example.test", "viewer", founder_user.id)
    mallory = call(app_client, service.sync_user, ISSUER, {"sub": "mallory", "email": "mallory@example.test", "email_verified": True})
    globex = call(app_client, service.create_tenant, "Globex", mallory)
    return {"acme": acme, "globex": {"id": str(globex.id), "slug": globex.slug}, "founder": founder,
            "alice": bearer(idp, "alice"), "victor": bearer(idp, "victor"), "mallory": bearer(idp, "mallory")}
