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
.PHONY: help seed api scoring-run test lint db-up db-down migrate clean

help:
	@printf 'Targets:\n'
	@printf '  make seed CITY=<slug>   Ingest city OSM into Postgres (default: cambridge)\n'
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

api: db-up migrate
	$(UV) run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

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
