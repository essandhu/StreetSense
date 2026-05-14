# StreetSense top-level Makefile.
#
# Thin wrappers around `uv run` and `pnpm` so contributors don't have to
# remember the underlying commands. Mirror this file in `tasks.ps1` for
# Windows users without `make` on PATH.
#
# Variables:
#   CITY=<slug>   The city config to load (default: cambridge). See
#                 config/cities/.
#   UV            How to invoke uv (default: `uv`; CI sets via env if uv is
#                 not on PATH).
#   PNPM          How to invoke pnpm (default: `pnpm`).
#
# Targets:
#   help          Print this list.
#   seed          Ingest the configured city's OSM extract into Postgres.
#   ingest-imagery Ingest street-level imagery for the configured city (Phase 3).
#   api           Start the FastAPI service (+ pg_tileserv where applicable).
#   scoring-run   Trigger a scoring run end-to-end. (Phase 1 stub.)
#   test          Run all tests (Python + frontend unit).
#   lint          Run all linters/formatters/typecheckers in check mode.
#   db-up         Start the data plane (Postgres + PostGIS + MinIO).
#   db-down       Stop and remove the data plane.
#   migrate       Apply Alembic migrations.
#   clean         Remove caches and build artifacts.

CITY ?= cambridge
UV   ?= uv
PNPM ?= pnpm

.DEFAULT_GOAL := help
.PHONY: help seed ingest-imagery ingest-incidents seed-model api scoring-run test lint db-up db-down migrate clean

help:
	@printf 'Targets:\n'
	@printf '  make seed CITY=<slug>   Ingest city OSM into Postgres (default: cambridge)\n'
	@printf '  make ingest-imagery CITY=<slug>  Ingest street-level imagery (Phase 3)\n'
	@printf '  make ingest-incidents CITY=<slug> Ingest historical road incidents (Phase 4)\n'
	@printf '  make seed-model         Upload perception ONNX artifact to MinIO (Phase 3)\n'
	@printf '  make api                Start FastAPI service\n'
	@printf '  make scoring-run        Trigger a scoring run (Phase 1: stub)\n'
	@printf '  make test               Run Python + frontend unit tests\n'
	@printf '  make lint               Lint/format-check/typecheck everything\n'
	@printf '  make db-up              Start data plane (Postgres + PostGIS + MinIO)\n'
	@printf '  make db-down            Stop the data plane\n'
	@printf '  make migrate            Apply Alembic migrations\n'
	@printf '  make clean              Remove caches and build artifacts\n'

db-up:
	docker compose up -d

db-down:
	docker compose down

migrate:
	$(UV) run alembic upgrade head

seed: db-up migrate
	$(UV) run python -m ingestion.cli seed --city $(CITY)

ingest-imagery: db-up migrate
	$(UV) run python -m ingestion.cli imagery --city $(CITY)

ingest-incidents: db-up migrate
	$(UV) run python -m ingestion.cli incidents --city $(CITY)

# Phase 3: upload the perception ONNX artifact to MinIO. Defaults to the
# stand-in; pass ARTIFACT=path/to/real-model.onnx to upload a different
# artifact produced by tools/perception/build_real_onnx.py.
ARTIFACT ?= tests/fixtures/perception/standin.onnx
ARTIFACT_NAME ?= lane-marking-standin
seed-model: db-up migrate
	$(UV) run python tools/perception/seed_model.py --artifact $(ARTIFACT) --name $(ARTIFACT_NAME)

api: db-up migrate
	# Route through scripts/serve_api.py so the asyncio event-loop policy
	# is pinned before uvicorn constructs its loop. On Windows the
	# default ProactorEventLoop is incompatible with psycopg's async
	# pool; the launcher fixes this silently.
	$(UV) run python -m scripts.serve_api --reload

scoring-run: db-up migrate
	$(UV) run python -m scoring.cli run --city $(CITY)

# --- Tests ----------------------------------------------------------------
test: test-py test-fe

test-py:
	$(UV) run pytest

test-fe:
	cd frontend && $(PNPM) test

# --- Lint -----------------------------------------------------------------
lint: lint-py lint-fe

lint-py:
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy

lint-fe:
	cd frontend && $(PNPM) lint
	cd frontend && $(PNPM) format:check
	cd frontend && $(PNPM) typecheck

# --- Housekeeping ---------------------------------------------------------
clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	rm -rf frontend/node_modules/.cache frontend/dist frontend/coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
