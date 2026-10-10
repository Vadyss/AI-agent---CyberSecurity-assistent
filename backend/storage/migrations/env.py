# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Alembic environment. The URL comes from DATABASE_URL, never from a file."""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from storage.models import Base

config = context.config
url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL", "")
if not url:
    raise SystemExit("DATABASE_URL is required to run migrations.")
config.set_main_option("sqlalchemy.url", url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.",
                                poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
