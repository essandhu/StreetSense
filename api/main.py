"""FastAPI application factory.

Mounts segment-detail, admin/freshness, and health routes. The vector tile
endpoint is served by `pg_tileserv` as a sibling process — see ADR 0002.

Phase 3 adds CORS middleware so the Vite dev server on a different port
can hit the API. `STREETSENSE_CORS_ORIGINS` env var (comma-separated)
overrides the dev default; production deployments tighten this in
Phase 5.

Phase 3 also pins the asyncio event-loop policy. On Windows the
default ``ProactorEventLoop`` is incompatible with psycopg's async
pool. The pin here is a defensive belt-and-braces — the
``scripts/serve_api.py`` launcher pins the uvicorn loop factory
*before* uvicorn constructs its loop, which is the actual fix.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db import close_pool
from api.routes import admin, runs, segments

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

log = structlog.get_logger(__name__)


# Dev-friendly default: any localhost origin. Production tightens via
# STREETSENSE_CORS_ORIGINS=https://streetsense.example,https://admin.streetsense.example
_DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173"
)


def _cors_origins() -> list[str]:
    raw = os.environ.get("STREETSENSE_CORS_ORIGINS", _DEFAULT_CORS_ORIGINS)
    return [o.strip() for o in raw.split(",") if o.strip()]


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
        # Phase 5 (5.0, non-breaking add): ``GET /runs/{run_a}/delta/{run_b}``
        # ships paginated per-segment deltas with full sub-score
        # decomposition and both runs' provenance bundles. Existing
        # endpoints unchanged.
        version="5.0.0",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(segments.router)
    app.include_router(admin.router)
    app.include_router(runs.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
