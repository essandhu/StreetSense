# StreetSense API + static frontend image — Phase 5 Task 1.3.
#
# Three stages:
#   1. ``frontend-build`` — Node 22 + pnpm 9. Installs frontend deps and
#      runs ``pnpm build`` to produce ``frontend/dist/``.
#   2. ``api-build``      — Debian-slim Python 3.12 + uv + Boost. Runs
#      ``uv sync --frozen --no-dev`` which triggers scikit-build-core to
#      compile the C++ propagator wheel (per ADR 0008's
#      "build-per-deploy" decision; layer caching on Fly's remote
#      builders makes unchanged C++ effectively free).
#   3. ``runtime``        — Same Python 3.12 slim base + the runtime
#      Boost shared lib + the prepared venv from stage 2 + the
#      frontend bundle from stage 1. ``main.py`` mounts
#      ``frontend/dist`` via FastAPI ``StaticFiles`` when present
#      (Task 1.4) so the API process serves both the JSON API and the
#      SPA, per the ADR 0008 deploy shape.
#
# Image layout under ``/app``:
#   /app/api/, /app/scoring/, /app/ingestion/, /app/db/  — Python code
#   /app/.venv/                                          — uv-managed venv
#   /app/frontend/dist/                                  — built SPA bundle
#
# Run:
#   docker build -f api/Dockerfile -t streetsense-api .
#   docker run -p 8000:8000 -e DATABASE_URL=... streetsense-api

# =========================================================================
# Stage 1 — frontend build
# =========================================================================
FROM node:22-bookworm-slim AS frontend-build

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    CI=1

# pnpm via corepack — bundled with the Node image, pinned via
# package.json's `packageManager` field (or top-level engines).
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate

WORKDIR /app/frontend

# Copy manifests first for a stable dependency layer.
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Bring in the rest of the frontend source.
COPY frontend/ ./

# Vite builds into ./dist by default.
RUN pnpm build

# =========================================================================
# Stage 2 — API + C++ propagator build
# =========================================================================
FROM python:3.12-slim-bookworm AS api-build

# Build-time deps: a C++17 compiler, CMake, Boost.Graph headers (header-
# only library — runtime stage doesn't need the shared lib, but does
# need libstdc++). git for any FetchContent the propagator uses
# (spdlog, GoogleTest pulled at configure time).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        git \
        libboost-graph-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv: fast Python package manager, replaces pip+venv. Pinned to a
# specific version so image reproducibility doesn't drift with the
# uv installer's "latest".
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dep manifests + the propagator's source for the editable install
# layer. The propagator wheel is built by scikit-build-core during
# ``uv sync``, so its source must be present.
COPY pyproject.toml uv.lock ./
COPY scoring/propagator/ ./scoring/propagator/

# Lockfile-driven install, no dev deps. This is the slow step (C++
# compile via scikit-build-core); layer-cached as long as
# pyproject.toml / uv.lock / scoring/propagator/ don't change.
RUN uv sync --frozen --no-dev

# Now the rest of the Python source. Edits here don't invalidate the
# heavy dep layer above.
COPY api/ ./api/
COPY scoring/ ./scoring/
COPY ingestion/ ./ingestion/
COPY db/ ./db/
COPY scripts/ ./scripts/
COPY config/ ./config/
COPY tools/ ./tools/
COPY alembic.ini ./

# =========================================================================
# Stage 3 — runtime
# =========================================================================
FROM python:3.12-slim-bookworm AS runtime

# Runtime libs: libboost-graph is header-only (no shared lib needed
# at runtime), but the propagator's pybind11 module is dynamically
# linked against libstdc++ which the slim image already has. Add
# CA certs + curl for the smoke / healthcheck path.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Default to noon UTC if no override; the bootstrap (Task 1.6)
    # overrides this for the initial scoring run.
    TZ=UTC

WORKDIR /app

# Bring across the prepared venv + the Python source.
COPY --from=api-build /app/.venv /app/.venv
COPY --from=api-build /app/api /app/api
COPY --from=api-build /app/scoring /app/scoring
COPY --from=api-build /app/ingestion /app/ingestion
COPY --from=api-build /app/db /app/db
COPY --from=api-build /app/scripts /app/scripts
COPY --from=api-build /app/config /app/config
COPY --from=api-build /app/tools /app/tools
COPY --from=api-build /app/alembic.ini /app/alembic.ini

# The built SPA bundle. main.py (Task 1.4) mounts this directory
# via FastAPI StaticFiles when present.
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

# Non-root runtime user. Fly Machines run as root by default, but
# making this explicit keeps the image portable to platforms that
# enforce non-root.
RUN useradd --create-home --shell /sbin/nologin --uid 1000 streetsense \
    && chown -R streetsense:streetsense /app
USER streetsense

EXPOSE 8000

# Use the existing serve_api launcher which pins the asyncio event
# loop policy correctly (Windows compatibility carries through but is
# inert on Linux; the launcher is the right entrypoint regardless).
CMD ["python", "-m", "scripts.serve_api", "--host", "0.0.0.0", "--port", "8000"]
