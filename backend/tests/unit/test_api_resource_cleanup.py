# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import logging

from fastapi.testclient import TestClient

from tests.unit.api_support import make_stub_app


def test_resource_cleanup_never_logs_exception_secrets(tmp_path, caplog):
    secret = "synthetic-secret-in-resource-exception"

    class FailingResource:
        async def close(self):
            raise ValueError(secret)

    app, _ = make_stub_app(tmp_path)
    with caplog.at_level(logging.ERROR), TestClient(app) as client:
        app.state.roles = FailingResource()
        assert client.get("/health").status_code == 200
    assert secret not in caplog.text
    assert any("ValueError" in record.getMessage() for record in caplog.records)
    assert all(record.exc_info is None for record in caplog.records)
