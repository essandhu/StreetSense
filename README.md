# StreetSense

A web-based platform that forecasts where and when road conditions will challenge ADAS perception systems — before incidents occur. StreetSense fuses OpenStreetMap road networks, street-level imagery, and solar geometry into a queryable, time-aware risk surface with per-segment explainable sub-scores.

## Status

Phase 1 of 5: **Ingestion, Storage, and Map** — in progress.

The demonstrable output of Phase 1 is a real road network on a map with stubbed (non-meaningful) risk coloring. Subsequent phases attach real scorers without re-architecting the pipeline:

| Phase | Output |
|---|---|
| 1 | Map of real road segments with stubbed risk coloring |
| 2 | Animated glare corridor across a day |
| 3 | Lane quality layer with click-through to source imagery |
| 4 | Composite risk layer with documented performance benchmark |
| 5 | Live publicly accessible instance with delta analysis |

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

## Quickstart (Phase 1)

Prerequisites: Docker, `uv` (Python), `pnpm` (Node 20+).

```bash
docker compose up -d            # Postgres 16 + PostGIS 3.4
make seed                       # Ingest one city (default: Cambridge, MA)
make api                        # FastAPI + pg_tileserv
cd frontend && pnpm dev         # http://localhost:5173
```

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

Python 3.12 (uv, ruff, mypy --strict, pytest, hypothesis) · PostgreSQL 16 + PostGIS 3.4 · FastAPI · pg_tileserv · React + TypeScript + Vite · MapLibre GL JS · Redux Toolkit (UI state) + TanStack Query (server state) · deck.gl (Phase 2) · Three.js (later) · D3 directly (later) · ONNX Runtime (Phase 3) · C++17 + Boost.Graph + pybind11 (Phase 4).

## License

TBD.
