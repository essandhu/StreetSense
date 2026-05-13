"""Pytest fixtures for the schema-invariant suite.

These tests run against a live Postgres+PostGIS — locally against the
docker-compose service, in CI against the GitHub Actions service container.
The DSN comes from $DATABASE_URL.

Two database roles are managed per test session:

- The **owner** (DATABASE_URL): owns the schema; runs `alembic upgrade head`.
- The **app role** ($POSTGRES_APP_USER): the role the API uses at runtime.
  UPDATE/DELETE on append-only tables must be revoked from it (verified by
  test_schema_invariants.py).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; integration test requires a running Postgres")
    return value


@pytest.fixture(scope="session")
def database_url() -> str:
    return _require_env("DATABASE_URL")


@pytest.fixture(scope="session")
def app_user() -> str:
    return os.environ.get("POSTGRES_APP_USER", "streetsense_app")


@pytest.fixture(scope="session")
def app_password() -> str:
    return os.environ.get("POSTGRES_APP_PASSWORD", "streetsense_app")


def _psycopg_dsn(url: str) -> str:
    """Strip SQLAlchemy driver prefix for raw psycopg use."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def migrated_db(database_url: str) -> Iterator[str]:
    """Run `alembic upgrade head` once per session; yield the raw psycopg DSN."""
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"alembic upgrade head failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return _psycopg_dsn(database_url)


@pytest.fixture
def owner_conn(migrated_db: str) -> Iterator[psycopg.Connection[Any]]:
    """Connection as the schema owner (DATABASE_URL credentials)."""
    with psycopg.connect(migrated_db) as conn:
        yield conn


@pytest.fixture
def app_conn(
    migrated_db: str,
    app_user: str,
    app_password: str,
) -> Iterator[psycopg.Connection[Any]]:
    """Connection as the runtime app role.

    The role is provisioned by migration 0001. UPDATE/DELETE on append-only
    tables must be revoked from this role at the schema level.
    """
    dsn = migrated_db
    for old, new in (
        ("://streetsense:", f"://{app_user}:"),
        ("streetsense@", f"{app_password}@"),
    ):
        if old in dsn:
            dsn = dsn.replace(old, new, 1)
    with psycopg.connect(dsn) as conn:
        yield conn
