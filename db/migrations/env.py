"""Alembic environment for StreetSense.

Reads DSN from the DATABASE_URL environment variable (the same variable
documented in `.env.example`), so the same migrations run identically in
local dev, CI, and prod.

PostGIS-aware: filters out PostGIS-managed tables from autogen so the
postgis extension's internal schema doesn't pollute diffs. Migrations
themselves are written by hand in Phase 1 — autogen is staged for future
phases when we may pull SQLAlchemy ORM models in.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# --- Alembic config -------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- DSN resolution -------------------------------------------------------
# Precedence: explicit alembic.ini override (only useful in tests) > env var.
_env_url = os.environ.get("DATABASE_URL")
if _env_url:
    config.set_main_option("sqlalchemy.url", _env_url)

# --- Autogen target metadata ---------------------------------------------
# Phase 1 writes migrations by hand. When a future phase adds SQLAlchemy ORM
# models, import their Base.metadata here.
target_metadata = None


# --- PostGIS-aware autogen filters ---------------------------------------
_POSTGIS_TABLES = frozenset(
    {
        "spatial_ref_sys",
        "geometry_columns",
        "geography_columns",
        "raster_columns",
        "raster_overviews",
    }
)


def _include_object(
    obj: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    """Skip PostGIS internal tables from autogen comparisons."""
    return not (type_ == "table" and name in _POSTGIS_TABLES)


# --- Runners --------------------------------------------------------------
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
