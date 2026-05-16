"""One-shot bootstrap for the deployed instance — Phase 5 Task 1.6.

Runs against the deployed Postgres + S3 (Fly Postgres + Tigris on the
primary path; self-hosted PG + MinIO on the fallback). Composes the
existing CLIs:

  1. ``alembic upgrade head`` — apply schema migrations.
  2. ``ingestion.cli seed --city <city>`` — load OSM road network.
  3. ``ingestion.cli imagery --city <city>`` — load street-level
     imagery references (optional; skip with ``--skip-imagery``).
  4. ``ingestion.cli incidents --city <city>`` — load historical
     incidents (optional; skip with ``--skip-incidents``).
  5. ``scoring.cli run --city <city>`` — produce the first
     ``scoring_runs`` row so the live URL has real data on first
     load. Plan §4.4 calls for a second manual run before the
     delta UI is meaningfully populated.

Idempotency:
  - Migrations: Alembic is idempotent by design.
  - Seed: re-running adds nothing if the OSM extract is already
    loaded (per existing ``cmd_seed`` semantics).
  - Imagery / incidents: incremental; re-runs pick up where they
    left off.
  - Scoring: each invocation produces a *new* ``scoring_runs`` row
    by design — re-running grows the history. That's the desired
    behavior; the bootstrap is one of N invocations over the
    deploy's lifetime.

Failure handling: any step that exits non-zero halts the bootstrap.
Structured logs at every transition so an operator tailing
``flyctl logs`` (or ``docker compose logs``) can spot the stuck
step.

Usage::

    python -m scripts.bootstrap_deploy --city cambridge
    python -m scripts.bootstrap_deploy --city cambridge --skip-imagery --skip-incidents

Env: requires ``DATABASE_URL``, plus the S3 / MinIO credentials the
imagery loader expects when imagery isn't skipped.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable, Sequence

import structlog

log = structlog.get_logger(__name__)


def _run_step(name: str, runner: Callable[[], int]) -> None:
    """Run one bootstrap step. Halts the bootstrap on non-zero exit."""
    started = time.monotonic()
    log.info("bootstrap.step_start", step=name)
    rc = runner()
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if rc != 0:
        log.error("bootstrap.step_failed", step=name, rc=rc, elapsed_ms=elapsed_ms)
        raise SystemExit(rc)
    log.info("bootstrap.step_ok", step=name, elapsed_ms=elapsed_ms)


def _exec_module(module: str, *argv: str) -> int:
    """Run a Python module subprocess, returning its exit code."""
    cmd = [sys.executable, "-m", module, *argv]
    log.info("bootstrap.exec", cmd=" ".join(cmd))
    completed = subprocess.run(cmd, check=False)
    return completed.returncode


def _exec(*cmd: str) -> int:
    log.info("bootstrap.exec", cmd=" ".join(cmd))
    completed = subprocess.run(list(cmd), check=False)
    return completed.returncode


def bootstrap(
    city: str,
    *,
    skip_imagery: bool = False,
    skip_incidents: bool = False,
    skip_scoring: bool = False,
) -> int:
    """Run the bootstrap sequence end-to-end. Returns the final exit code."""
    log.info(
        "bootstrap.start",
        city=city,
        skip_imagery=skip_imagery,
        skip_incidents=skip_incidents,
        skip_scoring=skip_scoring,
    )
    _run_step("migrate", lambda: _exec("alembic", "upgrade", "head"))
    _run_step("seed_osm", lambda: _exec_module("ingestion.cli", "seed", "--city", city))
    if not skip_imagery:
        _run_step(
            "ingest_imagery",
            lambda: _exec_module("ingestion.cli", "imagery", "--city", city),
        )
    else:
        log.info("bootstrap.step_skipped", step="ingest_imagery")
    if not skip_incidents:
        _run_step(
            "ingest_incidents",
            lambda: _exec_module("ingestion.cli", "incidents", "--city", city),
        )
    else:
        log.info("bootstrap.step_skipped", step="ingest_incidents")
    if not skip_scoring:
        _run_step("scoring_run", lambda: _exec_module("scoring.cli", "run", "--city", city))
    else:
        log.info("bootstrap.step_skipped", step="scoring_run")
    log.info("bootstrap.complete", city=city)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="streetsense-bootstrap", description=__doc__)
    parser.add_argument(
        "--city",
        required=True,
        help="City config slug (e.g., cambridge).",
    )
    parser.add_argument(
        "--skip-imagery",
        action="store_true",
        help="Skip the imagery ingestion step (heavy; first-deploy bandwidth saver).",
    )
    parser.add_argument(
        "--skip-incidents",
        action="store_true",
        help="Skip the incidents ingestion step.",
    )
    parser.add_argument(
        "--skip-scoring",
        action="store_true",
        help=(
            "Skip the initial scoring run. Use when ingesting alone; the "
            "scheduled cron (Phase 4 Task 4.2) will produce the first "
            "scoring run on its next firing."
        ),
    )
    args = parser.parse_args(argv)
    return bootstrap(
        args.city,
        skip_imagery=args.skip_imagery,
        skip_incidents=args.skip_incidents,
        skip_scoring=args.skip_scoring,
    )


if __name__ == "__main__":
    raise SystemExit(main())
