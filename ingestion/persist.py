"""Persistence layer for ingestion.

Writes `RoadSegment` iterables to PostgreSQL + PostGIS in a single
transactional batch. Idempotent on `osm_way_id`: re-ingesting the same way
updates its geometry and attributes in place (UPSERT), it does **not**
create a duplicate row.

`data_sources.last_ingested_at` is updated in the same transaction so the
freshness endpoint never reports a successful ingestion whose data was not
actually persisted.

Per CLAUDE.md, no `print` in shipped code — `structlog` events only.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

import psycopg
import structlog
from psycopg.types.json import Jsonb
from shapely import wkb

from ingestion.osm import RoadSegment, SnapshotMetadata

log = structlog.get_logger(__name__)


_INSERT_SEGMENT_SQL = """
INSERT INTO road_segments (osm_way_id, geometry, attrs, city_id)
VALUES (
    %(osm_way_id)s,
    ST_SetSRID(ST_GeomFromWKB(%(wkb)s), 4326),
    %(attrs)s,
    %(city_id)s
)
ON CONFLICT (osm_way_id) WHERE osm_way_id IS NOT NULL DO UPDATE
    SET geometry = EXCLUDED.geometry,
        attrs    = EXCLUDED.attrs,
        city_id  = EXCLUDED.city_id
"""

_ENSURE_UNIQUE_OSM_WAY_ID = """
CREATE UNIQUE INDEX IF NOT EXISTS road_segments_osm_way_id_uidx
    ON road_segments (osm_way_id)
    WHERE osm_way_id IS NOT NULL
"""

_UPSERT_DATA_SOURCE_SQL = """
INSERT INTO data_sources (name, last_ingested_at, metadata)
VALUES (%(name)s, now(), %(metadata)s)
ON CONFLICT (name) DO UPDATE
    SET last_ingested_at = EXCLUDED.last_ingested_at,
        metadata         = EXCLUDED.metadata
"""


def _to_psycopg_dsn(url: str) -> str:
    """Strip SQLAlchemy driver prefix for raw psycopg use."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _metadata_to_jsonb(meta: SnapshotMetadata) -> Jsonb:
    return Jsonb(
        {
            "source_url": meta.source_url,
            "osm_snapshot_date": meta.osm_snapshot_date.isoformat(),
            "size_bytes": meta.size_bytes,
            "sha256": meta.sha256,
            "local_path": str(meta.local_path),
        }
    )


def persist_road_segments(
    database_url: str,
    segments: Iterable[RoadSegment],
    snapshot: SnapshotMetadata,
    *,
    source_name: str = "osm",
    batch_size: int = 1000,
    city_id: UUID,
) -> int:
    """Persist `segments` and record the snapshot's ingestion timestamp.

    Args:
        database_url: SQLAlchemy- or psycopg-style DSN. SQLAlchemy prefix is
            stripped for raw psycopg use.
        segments: Stream of `RoadSegment` to insert. Iterated once.
        snapshot: Provenance of the data being ingested.
        source_name: `data_sources.name` to update. Defaults to "osm".
        batch_size: Number of rows per `executemany` call.
        city_id: City the segments belong to. Phase 4b: keyword-only and
            required. Resolved from --city <slug> by the CLI via
            ``ingestion.seed_cities.get_city_id_by_slug``.

    Returns:
        Number of rows written (each upsert counts as one).
    """
    dsn = _to_psycopg_dsn(database_url)
    written = 0

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # Ensure the UPSERT target index exists. road_segments was created
            # with a non-unique BTREE on osm_way_id in migration 0001; this
            # promotes it to UNIQUE (idempotent CREATE).
            cur.execute(_ENSURE_UNIQUE_OSM_WAY_ID)

        batch: list[dict[str, object]] = []

        def _flush() -> int:
            nonlocal batch
            if not batch:
                return 0
            with conn.cursor() as inner:
                inner.executemany(_INSERT_SEGMENT_SQL, batch)
            count = len(batch)
            batch = []
            return count

        for seg in segments:
            batch.append(
                {
                    "osm_way_id": seg.osm_way_id,
                    "wkb": wkb.dumps(seg.geometry),
                    "attrs": Jsonb(seg.attrs),
                    "city_id": city_id,
                }
            )
            if len(batch) >= batch_size:
                written += _flush()

        written += _flush()

        with conn.cursor() as cur:
            cur.execute(
                _UPSERT_DATA_SOURCE_SQL,
                {"name": source_name, "metadata": _metadata_to_jsonb(snapshot)},
            )

        conn.commit()

    log.info(
        "persist.done",
        source=source_name,
        rows=written,
        snapshot_date=snapshot.osm_snapshot_date.isoformat(),
        snapshot_url=snapshot.source_url,
    )
    return written


__all__ = ["persist_road_segments"]
