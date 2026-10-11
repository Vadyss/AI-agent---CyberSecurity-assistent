# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""The published API contract stays in sync with the code."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from orchestrator.main import create_app
from tests.fakes import test_settings

SNAPSHOT = Path(__file__).resolve().parents[3] / "Docs" / "api" / "openapi.json"


def test_committed_openapi_matches_the_code():
    import importlib.util

    spec = importlib.util.spec_from_file_location("export_openapi", SNAPSHOT.parents[2] / "backend/scripts/export_openapi.py")
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    assert json.loads(exporter.render()) == json.loads(SNAPSHOT.read_text()), \
        "The API changed: run `python backend/scripts/export_openapi.py` and commit Docs/api/openapi.json."


def test_contract_documents_authentication_and_errors():
    schema = json.loads(SNAPSHOT.read_text())
    assert set(schema["components"]["securitySchemes"]) == {"oidcIdToken", "apiKey"}
    for path, operations in schema["paths"].items():
        assert path.startswith("/v1/"), path
        for method, operation in operations.items():
            if path != "/v1/health":
                assert {"401", "403"} <= set(operation["responses"]), (method, path)
    assert schema["paths"]["/v1/health"]["get"]["security"] == []


def test_versioned_routes_and_deprecated_unversioned_routes(tmp_path):
    with TestClient(create_app(settings=test_settings(data_dir=tmp_path))) as client:
        for path in ("/v1/runs", "/api/v1/runs", "/m2m/v1/runs"):
            response = client.get(path)
            assert response.status_code == 200 and "Deprecation" not in response.headers, path
        legacy = client.get("/api/runs")
        assert legacy.status_code == 200 and legacy.headers["Deprecation"] == "true"
        assert "Deprecation" not in client.get("/health").headers
        assert client.get("/v1/openapi.json").json()["info"]["title"] == "Toolsmith API"
        assert client.get("/openapi.json").status_code == 404
