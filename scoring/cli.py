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
import json
import sys
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

import onnxruntime as ort
import psycopg
import structlog
from minio import Minio

from ingestion.config import get_database_url, load_city
from scoring.environmental.glare import GlareScorer
from scoring.perception.scorer import ImageryLoader, PerceptionScorer
from scoring.run import ScoringRun, ScoringRunConfig, default_24_hourly_samples

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
    dsn = _psycopg_dsn(database_url)

    def _load(segment_id: UUID) -> Iterable[tuple[str, bytes]]:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT provider_image_id, object_key
                FROM segment_imagery
                WHERE segment_id = %s
                ORDER BY sample_index
                """,
                (segment_id,),
            )
            rows = cur.fetchall()
        for provider_image_id, object_key in rows:
            response = minio.get_object(bucket, object_key)
            try:
                yield str(provider_image_id), response.read()
            finally:
                response.close()
                response.release_conn()

    return _load


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
    perception_model_version, model_bucket, model_object_key = _resolve_perception_model(
        database_url
    )
    imagery_window = _resolve_imagery_window(database_url)
    samples = default_24_hourly_samples(reference_day)

    minio = _minio_from_env()
    session = _load_model_session(minio, model_bucket, model_object_key)
    imagery_loader = _build_imagery_loader(database_url, minio, DEFAULT_MINIO_BUCKET_IMAGERY)

    run_config = ScoringRunConfig(
        temporal_samples=samples,
        osm_snapshot_date=osm_snapshot_date,
        perception_model_version=perception_model_version,
        imagery_capture_window=imagery_window,
        notes=f"city={config.name}; reference_day={reference_day.isoformat()}",
    )
    run = ScoringRun(
        config=run_config,
        scorers=[
            GlareScorer(),
            PerceptionScorer(session=session, imagery_loader=imagery_loader),
        ],
        database_url=database_url,
    )
    summary = run.execute()

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
        "stub_fallback_lane_marking_rows": stub_lane_count,
    }
    log.info("scoring_cli.summary", **summary_record)
    print(json.dumps({"event": "scoring_cli.summary", **summary_record}), file=sys.stdout)
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
