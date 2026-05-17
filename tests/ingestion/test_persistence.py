"""Integration tests for `ingestion.persist` — Task 1.4.4 (test-first).

Asserts that ingesting the tiny fixture produces:

- The expected `road_segments` rows with `geometry(LineString, 4326)`.
- Attributes preserved in `attrs jsonb`.
- A `data_sources` row whose `last_ingested_at` is bumped.
- Idempotency on re-ingestion (same osm_way_id ⇒ upsert, not duplicate).

These are integration tests — they require a running, migrated Postgres.
The `migrated_db` fixture in `tests/db/conftest.py` is reused.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from shapely.geometry import LineString

from ingestion.osm import RoadSegment, SnapshotMetadata
from ingestion.persist import persist_road_segments

pytestmark = pytest.mark.integration


SAMPLE_SEGMENTS = [
    RoadSegment(
        osm_way_id=42_001,
        geometry=LineString([(-71.095, 42.365), (-71.090, 42.367), (-71.085, 42.370)]),
        attrs={"highway": "primary", "name": "Sample A"},
    ),
    RoadSegment(
        osm_way_id=42_002,
        geometry=LineString([(-71.092, 42.372), (-71.088, 42.375)]),
        attrs={"highway": "residential"},
    ),
]


SAMPLE_METADATA = SnapshotMetadata(
    osm_snapshot_date=date(2026, 5, 13),
    source_url="file:///fixtures/tiny_extract.osm",
    local_path=Path("/fixtures/tiny_extract.osm"),
    size_bytes=4096,
    sha256="deadbeef",
)


@pytest.fixture(autouse=True)
def _clean_segment_tables(owner_conn: psycopg.Connection[Any]) -> None:
    """Tests run against a shared DB — wipe the relevant tables between runs.

    We can TRUNCATE here because we are the schema owner (the append-only
    REVOKE applies to the app role, not the owner).
    """
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments CASCADE")
        cur.execute("DELETE FROM data_sources WHERE name = 'osm'")
    owner_conn.commit()


def test_persists_segments_with_geometry_in_4326(
    owner_conn: psycopg.Connection[Any], database_url: str, cambridge_city_id: Any
) -> None:
    count = persist_road_segments(
        database_url, SAMPLE_SEGMENTS, SAMPLE_METADATA, source_name="osm", city_id=cambridge_city_id
    )
    assert count == 2

    with owner_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM road_segments WHERE ST_SRID(geometry) = 4326")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 2


def test_attrs_preserved_as_jsonb(
    owner_conn: psycopg.Connection[Any], database_url: str, cambridge_city_id: Any
) -> None:
    persist_road_segments(database_url, SAMPLE_SEGMENTS, SAMPLE_METADATA, city_id=cambridge_city_id)

    with owner_conn.cursor() as cur:
        cur.execute("SELECT osm_way_id, attrs FROM road_segments ORDER BY osm_way_id")
        rows = cur.fetchall()
    assert rows[0][0] == 42_001
    assert rows[0][1] == {"highway": "primary", "name": "Sample A"}
    assert rows[1][0] == 42_002
    assert rows[1][1] == {"highway": "residential"}


def test_data_sources_row_updated_with_last_ingested_at(
    owner_conn: psycopg.Connection[Any], database_url: str, cambridge_city_id: Any
) -> None:
    persist_road_segments(
        database_url, SAMPLE_SEGMENTS, SAMPLE_METADATA, source_name="osm", city_id=cambridge_city_id
    )

    with owner_conn.cursor() as cur:
        cur.execute("SELECT name, last_ingested_at, metadata FROM data_sources WHERE name = 'osm'")
        row = cur.fetchone()
    assert row is not None
    name, last_ingested_at, metadata = row
    assert name == "osm"
    assert last_ingested_at is not None
    # Provenance recorded in metadata jsonb.
    assert metadata.get("source_url") == SAMPLE_METADATA.source_url
    assert metadata.get("osm_snapshot_date") == SAMPLE_METADATA.osm_snapshot_date.isoformat()


def test_reingest_is_idempotent_on_osm_way_id(
    owner_conn: psycopg.Connection[Any], database_url: str, cambridge_city_id: Any
) -> None:
    persist_road_segments(database_url, SAMPLE_SEGMENTS, SAMPLE_METADATA, city_id=cambridge_city_id)
    persist_road_segments(database_url, SAMPLE_SEGMENTS, SAMPLE_METADATA, city_id=cambridge_city_id)

    with owner_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM road_segments")
        row = cur.fetchone()
        assert row is not None
        # Idempotent: same osm_way_ids must not duplicate rows.
        assert row[0] == 2
