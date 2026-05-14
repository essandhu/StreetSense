"""FastAPI application factory.

Mounts segment-detail, admin/freshness, and health routes. The vector tile
endpoint is served by `pg_tileserv` as a sibling process — see ADR 0002.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from api.db import close_pool
from api.routes import admin, segments

# psycopg's async pool requires SelectorEventLoop on Windows; the default
# Windows policy (ProactorEventLoop) breaks async libpq IO. Production runs
# on Linux where this is a no-op; this line is for parity with local dev
# and CI runners that happen to be Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

log = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app
    log.info("api.startup")
    yield
    await close_pool()
    log.info("api.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="StreetSense API",
        # Phase 3 (3.0, breaking): `confidence` reshapes from a scalar
        # float to a `ConfidenceIndicator` object so the UI can label
        # the limiting input; `imagery` ships in `SegmentDetail` with
        # pre-signed MinIO URLs.
        version="3.0.0",
        lifespan=_lifespan,
    )
    app.include_router(segments.router)
    app.include_router(admin.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
