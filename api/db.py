"""Database connection management for the FastAPI service.

A small async connection pool over psycopg 3. We don't reach for SQLAlchemy
in Phase 1 because every endpoint here issues exactly one or two parameter-
ized queries — the abstraction would cost more than it saves.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from psycopg_pool import AsyncConnectionPool


def _psycopg_dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Source .env or copy .env.example to .env.")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


_pool: AsyncConnectionPool | None = None


async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=_psycopg_dsn(),
            min_size=1,
            max_size=10,
            open=False,
        )
        await _pool.open()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def conn() -> AsyncIterator[psycopg.AsyncConnection]:
    pool = await get_pool()
    async with pool.connection() as connection:
        yield connection
