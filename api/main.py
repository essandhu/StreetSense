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
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from api.auth import BasicAuthMiddleware
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


_DEFAULT_FRONTEND_DIST = Path("/app/frontend/dist")


def _frontend_dist_path() -> Path | None:
    """Resolve the on-disk SPA bundle directory, or ``None`` if unavailable.

    Honors ``STREETSENSE_FRONTEND_DIST`` for env-driven overrides
    (tests, dev with a custom build path). Falls back to the deploy
    image's known location (``/app/frontend/dist``) only when it
    actually exists — a dev machine without the deploy layout
    returns ``None`` and the SPA mount is skipped.
    """
    override = os.environ.get("STREETSENSE_FRONTEND_DIST")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_dir() else None
    return _DEFAULT_FRONTEND_DIST if _DEFAULT_FRONTEND_DIST.is_dir() else None


class _SpaStaticFiles(StaticFiles):
    """``StaticFiles`` that falls back to ``index.html`` on 404.

    ``html=True`` only serves ``index.html`` for directory requests.
    The SPA also needs unknown *file* paths (``/methodology``,
    ``/admin/freshness-ui``, deep-links the user pastes into the URL
    bar) to render the shell so client-side routing can take over.
    Mirrors the ``try_files $uri /index.html`` pattern from
    ``frontend/Dockerfile``'s nginx config so both deploy shapes
    have identical SPA semantics.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


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
    # Basic-auth gate (Phase 5 Task 1.5). No-op when
    # STREETSENSE_BASIC_AUTH is unset; mandatory gate when set.
    # CORS sits *outside* auth so the browser's preflight OPTIONS
    # round-trip can complete without credentials (browsers
    # deliberately don't send Authorization on preflights).
    app.add_middleware(BasicAuthMiddleware)
    app.include_router(segments.router)
    app.include_router(admin.router)
    app.include_router(runs.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Phase 5 (Task 1.4): mount the SPA last so explicit API routes
    # win the routing lookup. ``html=True`` makes StaticFiles serve
    # ``index.html`` for any directory request (so deep-links to
    # ``/methodology`` render the SPA shell and let client-side
    # routing take over). Dev servers without a built bundle simply
    # don't mount; the API still works.
    spa_dir = _frontend_dist_path()
    if spa_dir is not None:
        app.mount("/", _SpaStaticFiles(directory=str(spa_dir), html=True), name="spa")
        log.info("api.spa_mounted", path=str(spa_dir))

    return app


app = create_app()
