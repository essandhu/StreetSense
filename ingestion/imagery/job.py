"""Imagery ingestion job.

Provider-agnostic pipeline that:

1. Loads ``road_segments`` rows from Postgres.
2. Derives waypoints along each segment's geometry at a configurable
   cadence (default: one waypoint per 50 m of segment length, capped at
   5 per segment — see Tech Note 1 in the Phase 3 spec).
3. Streams ``ImageryReference``s from a provider for those waypoints.
4. Downloads bytes, uploads to MinIO under
   ``<bucket>/<provider>/<provider_image_id>.<ext>``, and writes a
   ``segment_imagery`` row in Postgres batched ~1000 at a time.
5. Updates ``data_sources.last_ingested_at`` for ``imagery`` so
   ``/admin/freshness`` reflects the run.

Idempotency: rows already present (matched on the
``(provider, provider_image_id, segment_id)`` natural key) are
skipped — both the MinIO upload and the row write. Re-running the job
against an unchanged provider state is a no-op beyond a few list
calls.

Per CLAUDE.md, no ``print`` in shipped code — ``structlog`` events
only.
"""

from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar
from uuid import UUID

import psycopg
import structlog
from minio import Minio
from minio.error import S3Error
from psycopg.types.json import Jsonb
from shapely import wkb
from shapely.geometry import LineString

from ingestion.imagery.provider import ImageryProvider, Waypoint

log = structlog.get_logger(__name__)


# Persistence + upload SQL ---------------------------------------------------
_LOAD_SEGMENTS_SQL = """
SELECT id, ST_AsBinary(geometry) AS wkb
FROM road_segments
WHERE geometry IS NOT NULL
ORDER BY id
"""

_EXISTING_REFS_SQL = """
SELECT provider, provider_image_id, segment_id
FROM segment_imagery
WHERE provider = %(provider)s
"""

_INSERT_REF_SQL = """
INSERT INTO segment_imagery (
    segment_id, provider, provider_image_id, sample_index,
    capture_date, heading_deg, camera_params, object_key
)
VALUES (
    %(segment_id)s, %(provider)s, %(provider_image_id)s, %(sample_index)s,
    %(capture_date)s, %(heading_deg)s, %(camera_params)s, %(object_key)s
)
ON CONFLICT (provider, provider_image_id, segment_id) DO NOTHING
"""

_UPSERT_DATA_SOURCE_SQL = """
UPDATE data_sources
SET last_ingested_at = now()
WHERE name = 'imagery'
"""


# Public types ---------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ImageryIngestSummary:
    """Outcome of an ingestion run, suitable for printing or logging."""

    rows_inserted: int
    rows_skipped: int
    bytes_uploaded: int
    elapsed_seconds: float
    capture_date_min: date | None
    capture_date_max: date | None


@dataclass(frozen=True, slots=True)
class ImageryIngestConfig:
    """Tunables for the ingestion run.

    ``meters_per_sample`` and ``max_samples_per_segment`` together set the
    sampling cadence. The defaults match Tech Note 1: one image per 50 m,
    capped at 5 per segment to bound work per scoring run.

    ``max_segments`` caps the number of segments processed (useful for
    demo smokes and development). ``None`` = no cap.
    """

    meters_per_sample: float = 50.0
    max_samples_per_segment: int = 5
    within: tuple[date, date] | None = None
    bucket: str = "streetsense-imagery"
    insert_batch_size: int = 1000
    max_segments: int | None = None


# Object-store seam ----------------------------------------------------------
class _MinIOClient:
    """Thin wrapper around the minio SDK so the job can be unit-tested.

    The job depends on this concrete class; alternative object stores
    would slot in by sharing this method shape, but that's a follow-up
    (not in Phase 3 scope per spec "Out of Scope").
    """

    DEFAULT_ENDPOINT: ClassVar[str] = "localhost:9000"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        secure: bool = False,
    ) -> None:
        self._client = Minio(
            endpoint or os.environ.get("MINIO_ENDPOINT", self.DEFAULT_ENDPOINT),
            access_key=access_key
            or os.environ.get("MINIO_ACCESS_KEY")
            or os.environ.get("MINIO_ROOT_USER", "streetsense"),
            secret_key=secret_key
            or os.environ.get("MINIO_SECRET_KEY")
            or os.environ.get("MINIO_ROOT_PASSWORD", "streetsense"),
            secure=secure,
        )

    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        stream = io.BytesIO(data)
        self._client.put_object(
            bucket_name=bucket,
            object_name=key,
            data=stream,
            length=len(data),
            content_type=content_type,
        )

    def object_exists(self, bucket: str, key: str) -> bool:
        try:
            self._client.stat_object(bucket, key)
            return True
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                return False
            raise


# Waypoint derivation --------------------------------------------------------
def _segment_waypoints(
    segment_id: UUID,
    geometry: LineString,
    *,
    meters_per_sample: float,
    max_samples: int,
) -> list[Waypoint]:
    """Sample ``geometry`` at the configured cadence.

    Uses ``shapely.interpolate(..., normalized=True)`` so the cadence is
    a metric distance in **degrees** on the WGS84 LineString, which is
    not strictly meters — but for the ~50 m / 5-sample cap, the
    approximation is good enough at Cambridge latitudes (~110 km per
    degree of latitude; ~83 km per degree of longitude). Phase 4+ can
    revisit if a metric projection becomes necessary.
    """
    if geometry.is_empty:
        return []
    # Use the geodetic length approximation: 1 degree ≈ 111 km. For Cambridge
    # (42°N), 1 degree of longitude ≈ 82 km. We average to 100 km/deg as a
    # conservative midpoint — the sampling cadence is approximate by design.
    APPROX_KM_PER_DEGREE = 100.0
    length_meters = geometry.length * APPROX_KM_PER_DEGREE * 1000.0
    n_samples = min(
        max_samples,
        max(1, int(length_meters / meters_per_sample) + 1),
    )
    waypoints: list[Waypoint] = []
    for i in range(n_samples):
        # Evenly spaced fractions in [0, 1]. For n_samples == 1, sample
        # the midpoint; otherwise sample endpoints inclusive.
        fraction = 0.5 if n_samples == 1 else i / (n_samples - 1)
        point = geometry.interpolate(fraction, normalized=True)
        waypoints.append(
            Waypoint(
                lat=float(point.y),
                lon=float(point.x),
                segment_id=segment_id,
                sample_index=i,
            )
        )
    return waypoints


# Main entrypoint ------------------------------------------------------------
def ingest_imagery(
    *,
    database_url: str,
    provider: ImageryProvider,
    object_store: _MinIOClient | None = None,
    config: ImageryIngestConfig | None = None,
) -> ImageryIngestSummary:
    """Run the imagery ingestion job end-to-end. Returns a summary."""
    cfg = config or ImageryIngestConfig()
    store = object_store or _MinIOClient()
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    start = time.perf_counter()

    rows_inserted = 0
    rows_skipped = 0
    bytes_uploaded = 0
    capture_min: date | None = None
    capture_max: date | None = None

    with psycopg.connect(dsn) as conn:
        # Load segments + existing references in one transaction so the
        # job sees a consistent snapshot.
        with conn.cursor() as cur:
            cur.execute(_LOAD_SEGMENTS_SQL)
            segments: list[tuple[UUID, bytes]] = list(cur.fetchall())
            if cfg.max_segments is not None:
                segments = segments[: cfg.max_segments]

            cur.execute(_EXISTING_REFS_SQL, {"provider": provider.name})
            existing: set[tuple[str, str, UUID]] = {
                (row[0], row[1], row[2]) for row in cur.fetchall()
            }

        log.info(
            "imagery_ingest.start",
            provider=provider.name,
            segments=len(segments),
            existing=len(existing),
            meters_per_sample=cfg.meters_per_sample,
            max_samples_per_segment=cfg.max_samples_per_segment,
        )

        # Build waypoints for every segment up-front. Memory cost: a few
        # Waypoint instances per segment, ~80 bytes each — comfortable
        # for a city.
        waypoints: list[Waypoint] = []
        for segment_id, geom_wkb in segments:
            geometry = wkb.loads(bytes(geom_wkb))
            waypoints.extend(
                _segment_waypoints(
                    segment_id,
                    geometry,
                    meters_per_sample=cfg.meters_per_sample,
                    max_samples=cfg.max_samples_per_segment,
                )
            )

        # Stream provider responses through to MinIO + DB. We batch row
        # inserts to keep transaction sizes bounded.
        batch: list[dict[str, Any]] = []

        def _flush() -> int:
            nonlocal batch
            if not batch:
                return 0
            with conn.cursor() as inner:
                inner.executemany(_INSERT_REF_SQL, batch)
            count = len(batch)
            batch = []
            return count

        for reference in provider.fetch_for_waypoints(waypoints, within=cfg.within):
            key = (reference.provider, reference.provider_image_id, reference.segment_id)
            if key in existing:
                rows_skipped += 1
                continue

            object_key = f"{reference.provider}/{reference.provider_image_id}.jpg"
            full_object_key = object_key  # what we store in DB
            if not store.object_exists(cfg.bucket, object_key):
                image_bytes = provider.download_bytes(reference)
                store.put_bytes(cfg.bucket, object_key, image_bytes, "image/jpeg")
                bytes_uploaded += len(image_bytes)

            capture_min = (
                reference.capture_date
                if capture_min is None
                else min(capture_min, reference.capture_date)
            )
            capture_max = (
                reference.capture_date
                if capture_max is None
                else max(capture_max, reference.capture_date)
            )

            batch.append(
                {
                    "segment_id": reference.segment_id,
                    "provider": reference.provider,
                    "provider_image_id": reference.provider_image_id,
                    "sample_index": reference.sample_index,
                    "capture_date": reference.capture_date,
                    "heading_deg": reference.heading_deg,
                    "camera_params": Jsonb(reference.camera_params),
                    "object_key": full_object_key,
                }
            )
            existing.add(key)  # de-dup within the run too
            if len(batch) >= cfg.insert_batch_size:
                rows_inserted += _flush()

        rows_inserted += _flush()

        with conn.cursor() as cur:
            cur.execute(_UPSERT_DATA_SOURCE_SQL)

        conn.commit()

    elapsed = time.perf_counter() - start

    summary = ImageryIngestSummary(
        rows_inserted=rows_inserted,
        rows_skipped=rows_skipped,
        bytes_uploaded=bytes_uploaded,
        elapsed_seconds=elapsed,
        capture_date_min=capture_min,
        capture_date_max=capture_max,
    )
    log.info(
        "imagery_ingest.done",
        provider=provider.name,
        rows_inserted=rows_inserted,
        rows_skipped=rows_skipped,
        bytes_uploaded=bytes_uploaded,
        seconds=round(elapsed, 3),
        capture_date_min=capture_min.isoformat() if capture_min else None,
        capture_date_max=capture_max.isoformat() if capture_max else None,
    )
    return summary


__all__ = [
    "ImageryIngestConfig",
    "ImageryIngestSummary",
    "ingest_imagery",
]
