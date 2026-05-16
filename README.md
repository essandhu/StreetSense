# StreetSense

A web-based platform that forecasts where and when road conditions will challenge ADAS perception systems — before incidents occur. StreetSense fuses OpenStreetMap road networks, street-level imagery, and solar geometry into a queryable, time-aware risk surface with per-segment explainable sub-scores.

## Status

Phase 5 of 5: **Delta Analysis + Scheduled Re-scoring + Live Public URL** — in progress.

| Phase | Output | State |
|---|---|---|
| 1 | Map of real road segments with stubbed risk coloring | shipped |
| 2 | Animated glare corridor across a day | shipped |
| 3 | Lane quality layer with click-through to source imagery | shipped |
| 4 | Composite risk layer with documented performance benchmark | shipped |
| 5 | Live publicly accessible instance with delta analysis | in progress (Fly.io provisioning pending) |

**Phase 5 live URL:** _TBD — published after `flyctl deploy` returns the hostname. Frontend + JSON API behind one shared basic-auth credential. A "Methodology" page in the live UI explains how each number is computed; a "Delta" mode toggle compares any two scoring runs with a GPU-painted delta map, sorted largest-changes list, and a D3 histogram. The Phase 5 demo walkthrough lives in `docs/PHASE_5_DEMO.md`._

## Architecture at a glance

```
OSM PBF ──▶ ingestion ──▶ Postgres + PostGIS ──▶ pg_tileserv ──▶ FastAPI ──▶ MapLibre (React)
                              │
                              └── scoring runs (append-only, 6 reproducibility fields)
                                   │
                                   ├── environmental scorer (Python)         [Phase 2]
                                   ├── perception scorer  (Python + ONNX)    [Phase 3]
                                   └── network propagator (C++ + pybind11)   [Phase 4]
```

## Local quickstart

Prerequisites: Docker, `uv` (Python), `pnpm` (Node 20+), and a C++17 toolchain + CMake 3.22+ + Boost ≥ 1.81 with `boost-graph` headers (see `scoring/propagator/README.md`).

```bash
git submodule update --init --recursive
docker compose up -d            # Postgres 16 + PostGIS 3.4 + MinIO + pg_tileserv
uv sync                          # Python deps + builds the C++ propagator
make seed                        # Ingest one city (default: Cambridge, MA)
make scoring-run                 # First scoring run end-to-end
make api                         # FastAPI on :8000
cd frontend && pnpm install && pnpm dev    # http://localhost:5173
```

## Production deploy

Two shapes supported (see ADR 0008):

- **Fly.io (primary).** `api/Dockerfile` + `fly.toml`; `fly.scoring.toml` declares the weekly cron Machine. Operator steps documented in `fly.toml`'s header.
- **Single VPS + docker compose (fallback).** `docker-compose.prod.yml` + `infra/Caddyfile` + `infra/cron.d/streetsense`. Caddy fronts the API + pg_tileserv with automatic Let's Encrypt TLS.

Both shapes use basic-auth gated on `STREETSENSE_BASIC_AUTH=user:bcrypt-hash` (the gate lives inside the FastAPI app, identical contract across hosts). Bootstrap the deployed Postgres with `python -m scripts.bootstrap_deploy --city cambridge`; gate the deploy with `python -m scripts.deploy_smoke --base-url https://<host>`.

## Layout

```
streetsense/
├── ingestion/           # Python: OSM, imagery, solar, incident adapters
├── api/                 # Python: FastAPI service
├── scoring/             # Python (env/perception) + C++ (propagator)
├── frontend/            # TypeScript + React + Vite + MapLibre
├── db/                  # Alembic migrations (forward-only)
├── infra/               # IaC (Phase 5)
├── benchmarks/          # Propagator perf history, tile latency, pan/zoom
├── config/              # City configs (bbox + Geofabrik URL)
└── tests/               # Cross-cutting integration tests
```

## Tech stack

Python 3.12 (uv, ruff, mypy --strict, pytest, hypothesis) · PostgreSQL 16 + PostGIS 3.4 · FastAPI · pg_tileserv · React + TypeScript + Vite · MapLibre GL JS + deck.gl · Redux Toolkit (UI state) + TanStack Query (server state) · D3 directly (charts) · ONNX Runtime (Phase 3 perception scorer) · C++17 + Boost.Graph + pybind11 (Phase 4 propagator) · bcrypt (Phase 5 basic auth) · Fly.io (Phase 5 hosting target, ADR 0008) · Playwright (E2E + frame-budget benchmarks).

## License

TBD.
