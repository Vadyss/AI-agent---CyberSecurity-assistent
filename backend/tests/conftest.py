# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
from __future__ import annotations

import socket
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def offline_test_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_DELAY_MS", "0")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "")
    monkeypatch.setenv("APIFY_TOKEN", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("EXAMINER_ENABLED", "false")
    monkeypatch.delenv("MOCK_SCENARIO", raising=False)
    original = socket.socket.connect

    def guarded_connect(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("A test attempted to connect outside localhost.")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture
def client(tmp_path):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from orchestrator.main import create_app
    from tests.fakes import InProcessSandbox, make_settings

    settings = replace(make_settings(), llm_provider="mock", mock_delay_ms=0, data_dir=tmp_path / "data")
    with TestClient(create_app(settings=settings, sandbox=InProcessSandbox())) as client:
        yield client


BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def postgres_url():
    """A migrated PostgreSQL database; scripts/test.sh always provides one."""
    import os
    import subprocess
    import sys

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        if os.environ.get("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("REQUIRE_POSTGRES_TESTS=1 but TEST_DATABASE_URL is not set.")
        pytest.skip("TEST_DATABASE_URL is not set; run backend/scripts/test.sh for PostgreSQL tests.")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND_DIR, check=True,
                   env={**os.environ, "DATABASE_URL": url})
    return url


async def _truncate(database) -> None:
    import sqlalchemy as sa
    from storage.models import Base

    async with database.engine.begin() as connection:
        tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
        await connection.execute(sa.text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture(params=["sqlite", "postgres"])
async def database(request):
    """The same storage tests run on in-memory SQLite and on migrated PostgreSQL."""
    from storage.db import Database

    if request.param == "sqlite":
        db = Database("sqlite+aiosqlite://")
        await db.create_schema()
    else:
        db = Database(request.getfixturevalue("postgres_url"), pool_size=2)
        await _truncate(db)
    yield db
    if request.param == "postgres":
        await _truncate(db)
    await db.close()
