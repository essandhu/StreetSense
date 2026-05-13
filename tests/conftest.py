"""Shared pytest fixtures for the test suite.

DB-related fixtures live here so any test subpackage (`tests/db`,
`tests/ingestion`, `tests/api`) can request them without explicit plugin
wiring. Connections require a running, migrated Postgres — pass DATABASE_URL
or run via `make test`.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# psycopg's async pool needs SelectorEventLoop on Windows. Set the policy
# globally for the test session so any async test (api/, future ones) gets
# a compatible loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """Override pytest-asyncio's event loop policy to be SelectorEventLoop on Windows."""
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


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
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def migrated_db(database_url: str) -> str:
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
    with psycopg.connect(migrated_db) as conn:
        yield conn


@pytest.fixture
def app_conn(
    migrated_db: str,
    app_user: str,
    app_password: str,
) -> Iterator[psycopg.Connection[Any]]:
    dsn = migrated_db
    for old, new in (
        ("://streetsense:", f"://{app_user}:"),
        ("streetsense@", f"{app_password}@"),
    ):
        if old in dsn:
            dsn = dsn.replace(old, new, 1)
    with psycopg.connect(dsn) as conn:
        yield conn
