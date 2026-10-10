# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Async engine and sessions. PostgreSQL in every deployment; SQLite only in tests."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import AsyncIterator

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .models import Base

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


class Database:
    def __init__(self, url: str, *, pool_size: int = 10) -> None:
        if url.startswith("sqlite"):
            # One shared in-memory connection, so every session sees the same data.
            self.engine = create_async_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})

            @sa.event.listens_for(self.engine.sync_engine, "connect")
            def foreign_keys(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")
        elif url.startswith("postgresql+psycopg://"):
            self.engine = create_async_engine(url, pool_pre_ping=True, pool_size=pool_size, max_overflow=pool_size)
        else:
            raise ValueError("DATABASE_URL must use postgresql+psycopg:// (or sqlite in tests).")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        # SQLite (tests) shares one connection; interleaved transactions on it
        # would see or roll back each other's work, so sessions take turns.
        self._serial = asyncio.Lock() if self.is_sqlite else None

    @property
    def is_sqlite(self) -> bool:
        return self.engine.dialect.name == "sqlite"

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A unit of work: commits on success, rolls back on any error. Never nest sessions."""
        async with self._serial or nullcontext():
            async with self.sessions() as session:
                try:
                    yield session
                    await session.commit()
                except BaseException:
                    await session.rollback()
                    raise

    async def create_schema(self) -> None:
        """Tests and ephemeral development only; deployments run Alembic migrations."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(sa.text("SELECT 1"))
            return True
        except Exception:
            return False

    async def schema_is_current(self) -> bool:
        """True when the database is at the newest Alembic revision."""
        if self.is_sqlite:
            return True
        try:
            async with self.engine.connect() as connection:
                current = (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalars().all()
        except Exception:
            return False
        return set(current) == {head_revision()}

    async def close(self) -> None:
        await self.engine.dispose()


def head_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError("Migrations must have exactly one head.")
    return heads[0]
