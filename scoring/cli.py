"""Scoring CLI — entrypoint for `make scoring-run`.

Usage:
    python -m scoring.cli run --city cambridge [--day 2025-06-21]

Behavior:

1. Loads `config/cities/<city>.yaml` and resolves the OSM snapshot date
   from the `osm` row in `data_sources` (written by `make seed`).
2. Builds a 24-hourly temporal-sample schedule for the chosen reference
   day (defaults to 2025-06-21, the summer solstice — picked because it
   produces non-degenerate glare on east-west arteries in Cambridge).
3. Constructs a `ScoringRun` with the glare scorer registered.
4. Executes; prints a structured summary at the end.

Every stage logs a discrete structlog event with timings.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime

import psycopg
import structlog

from ingestion.config import get_database_url, load_city
from scoring.environmental.glare import GlareScorer
from scoring.run import (
    ScoringRun,
    ScoringRunConfig,
    default_24_hourly_samples,
)

log = structlog.get_logger(__name__)

DEFAULT_REFERENCE_DAY = date(2025, 6, 21)  # Summer solstice — see module docstring.


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )


def _resolve_osm_snapshot_date(database_url: str) -> date:
    """Read `data_sources.metadata->>'osm_snapshot_date'` for the `osm` row."""
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT metadata FROM data_sources WHERE name = 'osm'")
        row = cur.fetchone()
    if row is None:
        raise RuntimeError(
            "No `osm` row in `data_sources`. Run `make seed` before `make scoring-run`."
        )
    meta = row[0] or {}
    snapshot = meta.get("osm_snapshot_date")
    if not snapshot:
        raise RuntimeError("`data_sources.osm.metadata.osm_snapshot_date` is missing.")
    return date.fromisoformat(snapshot)


def cmd_run(city: str, reference_day: date) -> int:
    _configure_logging()
    config = load_city(city)
    database_url = get_database_url()

    log.info(
        "scoring_cli.start",
        city=config.name,
        reference_day=reference_day.isoformat(),
    )

    osm_snapshot_date = _resolve_osm_snapshot_date(database_url)
    samples = default_24_hourly_samples(reference_day)

    run_config = ScoringRunConfig(
        temporal_samples=samples,
        osm_snapshot_date=osm_snapshot_date,
        notes=f"city={config.name}; reference_day={reference_day.isoformat()}",
    )
    run = ScoringRun(
        config=run_config,
        scorers=[GlareScorer()],
        database_url=database_url,
    )
    summary = run.execute()

    # Emit a JSON summary line so log scrapers can pick it up.
    print(
        json.dumps(
            {
                "event": "scoring_cli.summary",
                "run_id": str(summary.run_id),
                "scoring_run_timestamp": summary.scoring_run_timestamp.isoformat(),
                "rows_written": summary.rows_written,
                "segments_processed": summary.segments_processed,
                "temporal_samples": summary.temporal_samples_count,
                "seconds_elapsed": round(summary.seconds_elapsed, 3),
            },
        ),
        file=sys.stdout,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="streetsense-scoring")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="Execute one scoring run for the city.")
    run_p.add_argument("--city", required=True, help="City config slug (e.g., cambridge).")
    run_p.add_argument(
        "--day",
        default=DEFAULT_REFERENCE_DAY.isoformat(),
        help="ISO-8601 date for the 24-hourly sample schedule.",
    )
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args.city, datetime.fromisoformat(args.day).date())
    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
