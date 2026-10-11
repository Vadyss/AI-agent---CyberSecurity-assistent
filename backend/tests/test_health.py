# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from fastapi.testclient import TestClient

from orchestrator.main import create_app
from tests.fakes import make_settings


def test_health(tmp_path):
    client = TestClient(create_app(settings=make_settings(data_dir=tmp_path)))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "contract_version": 1}
