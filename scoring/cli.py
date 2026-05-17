"""Scoring CLI — entrypoint for `make scoring-run`.

Usage:
    python -m scoring.cli run --city cambridge [--day 2025-06-21]

Behavior:

1. Loads `config/cities/<city>.yaml` and resolves the OSM snapshot date
   from the `osm` row in `data_sources` (written by `make seed`).
2. Resolves the perception model version from
   `data_sources.perception_model.metadata.perception_model_version`
   (written by `make seed-model`); loads the ONNX bytes from MinIO.
3. Computes the imagery_capture_window from
   `min/max(segment_imagery.capture_date)` (populated by
   `make ingest-imagery`).
4. Builds a 24-hourly temporal-sample schedule for the chosen
   reference day (defaults to 2025-06-21 — summer solstice — picked
   for non-degenerate glare on east-west arteries).
5. Constructs a `ScoringRun` with GlareScorer + PerceptionScorer.
6. Executes; logs structured per-stage events including a
   stub-fallback count from `segment_scores` for the new run.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import onnxruntime as ort
import psycopg
import streetsense_propagator
import structlog
from minio import Minio

from ingestion.config import get_database_url, load_city
from ingestion.seed_cities import get_city_id_by_slug
from scoring.environmental.glare import GlareScorer
from scoring.historical.scorer import HistoricalCorrelationScorer
from scoring.junction.scorer import JunctionComplexityScorer
from scoring.perception.scorer import ImageryLoader, PerceptionScorer
from scoring.phase4_loaders import make_incident_loader, make_topology_loader
from scoring.phase4_run import execute_phase4_scoring_run
from scoring.propagator.runner import PHASE_4_DEFAULT_STRATEGY
from scoring.run import ScoringRunConfig, default_24_hourly_samples

log = structlog.get_logger(__name__)

DEFAULT_REFERENCE_DAY = date(2025, 6, 21)
DEFAULT_MINIO_BUCKET_IMAGERY = "streetsense-imagery"
DEFAULT_MINIO_BUCKET_MODELS = "streetsense-models"


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _resolve_osm_snapshot_date(database_url: str) -> date:
    with psycopg.connect(_psycopg_dsn(database_url)) as conn, conn.cursor() as cur:
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


def _resolve_perception_model(database_url: str) -> tuple[str, str, str]:
    """Return ``(perception_model_version, bucket, object_key)``."""
    with psycopg.connect(_psycopg_dsn(database_url)) as conn, conn.cursor() as cur:
        cur.execute("SELECT metadata FROM data_sources WHERE name = 'perception_model'")
        row = cur.fetchone()
    if row is None or not row[0] or not row[0].get("object_key"):
        raise RuntimeError(
            "No `perception_model` row in `data_sources`. Run `make seed-model` first."
        )
    meta: dict[str, Any] = row[0]
    return (
        meta["perception_model_version"],
        meta.get("bucket", DEFAULT_MINIO_BUCKET_MODELS),
        meta["object_key"],
    )


def _resolve_imagery_window(database_url: str) -> tuple[date, date]:
    with psycopg.connect(_psycopg_dsn(database_url)) as conn, conn.cursor() as cur:
        cur.execute("SELECT min(capture_date), max(capture_date) FROM segment_imagery")
        row = cur.fetchone()
    if row is None or row[0] is None:
        raise RuntimeError(
            "`segment_imagery` is empty. Run `make ingest-imagery` before `make scoring-run`."
        )
    return (row[0], row[1])


def _stub_fallback_count(database_url: str, run_id: UUID) -> int:
    with psycopg.connect(_psycopg_dsn(database_url)) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM segment_scores
            WHERE scoring_run_id = %s
              AND is_stub_lane_marking = true
            """,
            (run_id,),
        )
        row = cur.fetchone()
    assert row is not None
    return int(row[0])


def _minio_from_env() -> Minio:
    import os

    return Minio(
        os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.environ.get("MINIO_ROOT_USER", "streetsense"),
        secret_key=os.environ.get("MINIO_ROOT_PASSWORD", "streetsense"),
        secure=False,
    )


def _load_model_session(minio: Minio, bucket: str, object_key: str) -> ort.InferenceSession:
    response = minio.get_object(bucket, object_key)
    try:
        payload = response.read()
    finally:
        response.close()
        response.release_conn()
    return ort.InferenceSession(payload, providers=["CPUExecutionProvider"])


def _build_imagery_loader(database_url: str, minio: Minio, bucket: str) -> ImageryLoader:
    """Build a per-segment imagery loader.

    Pre-loads the full `segment_imagery` index (one query at run start,
    bounded by row count) and keys it by segment_id. Per-segment loads
    are then just a dict lookup + per-image MinIO `get_object`. A
    previous implementation opened a fresh psycopg connection per
    segment — that cost dominated the scoring-run wall-clock at
    city scale (~100 ms x 36 k segments = ~1 h of connection setup
    alone). The in-memory index trades a few MB of RAM for an order
    of magnitude in wall-clock.
    """
    dsn = _psycopg_dsn(database_url)
    index: dict[UUID, list[tuple[str, str]]] = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT segment_id, provider_image_id, object_key
            FROM segment_imagery
            ORDER BY segment_id, sample_index
            """,
        )
        for seg_id, provider_image_id, object_key in cur.fetchall():
            index.setdefault(seg_id, []).append((str(provider_image_id), str(object_key)))
    log.info(
        "imagery_loader.indexed",
        segments_with_imagery=len(index),
        total_rows=sum(len(v) for v in index.values()),
    )

    def _load(segment_id: UUID) -> Iterable[tuple[str, bytes]]:
        rows = index.get(segment_id)
        if not rows:
            return
        for provider_image_id, object_key in rows:
            response = minio.get_object(bucket, object_key)
            try:
                yield provider_image_id, response.read()
            finally:
                response.close()
                response.release_conn()

    return _load


def _resolve_incident_window(database_url: str) -> tuple[datetime, datetime] | None:
    """Return ``(min(incident_at), max(incident_at))`` from incidents, or None."""
    with psycopg.connect(_psycopg_dsn(database_url)) as conn, conn.cursor() as cur:
        cur.execute("SELECT min(incident_at), max(incident_at) FROM incidents")
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return (row[0], row[1])


def cmd_run(city: str, reference_day: date) -> int:
    _configure_logging()
    config = load_city(city)
    database_url = get_database_url()
    city_id = get_city_id_by_slug(database_url, config.slug)

    log.info(
        "scoring_cli.start",
        city=config.slug,
        city_id=str(city_id),
        reference_day=reference_day.isoformat(),
    )

    osm_snapshot_date = _resolve_osm_snapshot_date(database_url)
    perception_model_version, model_bucket, model_object_key = _resolve_perception_model(
        database_url
    )
    imagery_window = _resolve_imagery_window(database_url)
    samples = default_24_hourly_samples(reference_day)
    incident_window = _resolve_incident_window(database_url)

    minio = _minio_from_env()
    session = _load_model_session(minio, model_bucket, model_object_key)
    imagery_loader = _build_imagery_loader(database_url, minio, DEFAULT_MINIO_BUCKET_IMAGERY)

    # Build the Phase 4 scorer set: glare (Phase 2), perception (Phase 3),
    # junction-complexity + historical-correlation (both new in Phase 4).
    # JunctionComplexity + Historical bind to PostGIS through the
    # phase4_loaders' eager-load helpers so per-segment lookups stay
    # in-memory.
    propagation_algorithm_version = f"{PHASE_4_DEFAULT_STRATEGY}-{streetsense_propagator.version}"

    # The historical scorer needs the scoring-run's reference timestamp
    # for recency weighting. Use the noon UTC of the reference day so
    # the decay is consistent across all 24 samples.
    run_at = datetime(reference_day.year, reference_day.month, reference_day.day, 12, tzinfo=UTC)

    with psycopg.connect(_psycopg_dsn(database_url)) as conn:
        topology_loader = make_topology_loader(conn, city_id=city_id)
        incident_loader = make_incident_loader(conn, city_id=city_id)

    junction_scorer = JunctionComplexityScorer(topology_loader=topology_loader)
    historical_scorer = HistoricalCorrelationScorer(
        incident_loader=incident_loader,
        run_at=run_at,
    )

    run_config = ScoringRunConfig(
        temporal_samples=samples,
        osm_snapshot_date=osm_snapshot_date,
        city_id=city_id,
        perception_model_version=perception_model_version,
        imagery_capture_window=imagery_window,
        propagation_algorithm_version=propagation_algorithm_version,
        notes=f"city={config.slug}; reference_day={reference_day.isoformat()}",
    )
    summary = execute_phase4_scoring_run(
        config=run_config,
        scorers=[
            GlareScorer(),
            PerceptionScorer(session=session, imagery_loader=imagery_loader),
            junction_scorer,
            historical_scorer,
        ],
        database_url=database_url,
    )

    stub_lane_count = _stub_fallback_count(database_url, summary.run_id)

    summary_record: dict[str, Any] = {
        "run_id": str(summary.run_id),
        "scoring_run_timestamp": summary.scoring_run_timestamp.isoformat(),
        "rows_written": summary.rows_written,
        "segments_processed": summary.segments_processed,
        "temporal_samples": summary.temporal_samples_count,
        "seconds_elapsed": round(summary.seconds_elapsed, 3),
        "perception_model_version": perception_model_version,
        "imagery_capture_window": [
            imagery_window[0].isoformat(),
            imagery_window[1].isoformat(),
        ],
        "incidents_window": [
            incident_window[0].isoformat(),
            incident_window[1].isoformat(),
        ]
        if incident_window
        else None,
        "propagation_algorithm_version": propagation_algorithm_version,
        "propagation_total_seconds": round(summary.propagation_total_seconds, 3),
        "propagation_per_hour_seconds": [round(s, 4) for s in summary.propagation_per_hour_seconds],
        "composite_weights": dict(summary.composite_weights),
        "stub_fallback_lane_marking_rows": stub_lane_count,
    }
    log.info("scoring_cli.summary", **summary_record)
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
