"""Ingestion CLI — entrypoint for `make seed`.

Usage:
    python -m ingestion.cli seed --city cambridge

Behavior:

1. Loads `config/cities/<city>.yaml` and validates it.
2. Builds a `GeofabrikOSMSource` from the city's URL.
3. `fetch()` — downloads (or reuses the cached) PBF.
4. `parse()` — streams highway ways inside the bbox.
5. `persist_road_segments()` — upserts to PostGIS and updates
   `data_sources.last_ingested_at`.

Every stage logs a discrete structlog event with timings so observability
exists from day one (per CLAUDE.md).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence

import structlog

from ingestion.config import get_database_url, load_city
from ingestion.osm.osmium_adapter import GeofabrikOSMSource
from ingestion.persist import persist_road_segments

log = structlog.get_logger(__name__)


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )


def cmd_seed(city: str) -> int:
    _configure_logging()
    config = load_city(city)
    database_url = get_database_url()

    log.info("seed.start", city=config.name, bbox=list(config.bbox))

    source = GeofabrikOSMSource(url=config.geofabrik_extract_url)
    cache_path = config.resolved_cache_path

    t0 = time.perf_counter()
    metadata = source.fetch(config.bbox, cache_path)
    t_fetch = time.perf_counter() - t0
    log.info(
        "seed.fetch.done",
        path=str(metadata.local_path),
        size=metadata.size_bytes,
        snapshot_date=metadata.osm_snapshot_date.isoformat(),
        seconds=round(t_fetch, 3),
    )

    t1 = time.perf_counter()
    segments = source.parse(metadata.local_path, config.bbox)
    written = persist_road_segments(
        database_url,
        segments,
        metadata,
        source_name="osm",
    )
    t_persist = time.perf_counter() - t1
    log.info(
        "seed.persist.done",
        rows=written,
        seconds=round(t_persist, 3),
    )

    log.info(
        "seed.done",
        city=config.name,
        rows=written,
        total_seconds=round(t_fetch + t_persist, 3),
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="streetsense-ingest")
    sub = parser.add_subparsers(dest="cmd", required=True)

    seed = sub.add_parser("seed", help="Ingest one city's OSM extract.")
    seed.add_argument("--city", required=True, help="City config slug (e.g., cambridge).")

    args = parser.parse_args(argv)
    if args.cmd == "seed":
        return cmd_seed(args.city)
    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
