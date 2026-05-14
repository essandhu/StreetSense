"""Ingestion CLI — entrypoint for `make seed` and `make ingest-imagery`.

Usage:
    python -m ingestion.cli seed --city cambridge
    python -m ingestion.cli imagery --city cambridge

Behavior (``seed``):

1. Loads `config/cities/<city>.yaml` and validates it.
2. Builds a `GeofabrikOSMSource` from the city's URL.
3. `fetch()` — downloads (or reuses the cached) PBF.
4. `parse()` — streams highway ways inside the bbox.
5. `persist_road_segments()` — upserts to PostGIS and updates
   `data_sources.last_ingested_at`.

Behavior (``imagery``, Phase 3):

1. Loads the city config (currently unused beyond identification —
   the job ranges over every `road_segments` row).
2. Constructs a `MapillaryProvider` from `MAPILLARY_ACCESS_TOKEN`.
3. Runs `ingest_imagery` which derives waypoints from each segment's
   geometry, fetches references, downloads bytes, uploads to MinIO,
   and writes `segment_imagery` rows.

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
from ingestion.imagery.job import ImageryIngestConfig, ingest_imagery
from ingestion.imagery.mapillary import MapillaryProvider
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


def cmd_imagery(city: str) -> int:
    _configure_logging()
    config = load_city(city)
    database_url = get_database_url()

    log.info("imagery.start", city=config.name)

    t0 = time.perf_counter()
    with MapillaryProvider() as provider:
        summary = ingest_imagery(
            database_url=database_url,
            provider=provider,
            config=ImageryIngestConfig(),
        )
    t_total = time.perf_counter() - t0

    log.info(
        "imagery.done",
        city=config.name,
        rows_inserted=summary.rows_inserted,
        rows_skipped=summary.rows_skipped,
        bytes_uploaded=summary.bytes_uploaded,
        capture_date_min=(
            summary.capture_date_min.isoformat() if summary.capture_date_min else None
        ),
        capture_date_max=(
            summary.capture_date_max.isoformat() if summary.capture_date_max else None
        ),
        total_seconds=round(t_total, 3),
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="streetsense-ingest")
    sub = parser.add_subparsers(dest="cmd", required=True)

    seed = sub.add_parser("seed", help="Ingest one city's OSM extract.")
    seed.add_argument("--city", required=True, help="City config slug (e.g., cambridge).")

    imagery = sub.add_parser(
        "imagery",
        help="Ingest street-level imagery references for the configured city (Phase 3).",
    )
    imagery.add_argument(
        "--city", required=True, help="City config slug (e.g., cambridge)."
    )

    args = parser.parse_args(argv)
    if args.cmd == "seed":
        return cmd_seed(args.city)
    if args.cmd == "imagery":
        return cmd_imagery(args.city)
    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
