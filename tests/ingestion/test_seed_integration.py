"""End-to-end seed integration test — Task 1.4.7.

Runs the same code path `make seed` uses, but against the committed
fixture (not Geofabrik). The fixture is too small for a real perf test;
its purpose is to assert the *pipeline shape* is correct.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from ingestion.osm.osmium_adapter import OsmiumOSMSource
from ingestion.persist import persist_road_segments

pytestmark = pytest.mark.integration


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_extract.osm"
FIXTURE_BBOX = (-71.10, 42.36, -71.08, 42.38)


@pytest.fixture(autouse=True)
def _wipe_tables(owner_conn: psycopg.Connection[Any]) -> None:
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments CASCADE")
        cur.execute("DELETE FROM data_sources WHERE name = 'osm'")
    owner_conn.commit()


def test_full_seed_pipeline_against_fixture(
    owner_conn: psycopg.Connection[Any], database_url: str
) -> None:
    """fetch → parse → persist against the tiny fixture mirrors `make seed`."""
    adapter = OsmiumOSMSource(
        prefetched=FIXTURE_PATH, snapshot_date_for_prefetched=date(2026, 5, 1)
    )

    metadata = adapter.fetch(FIXTURE_BBOX, FIXTURE_PATH)
    segments = adapter.parse(metadata.local_path, FIXTURE_BBOX)

    written = persist_road_segments(database_url, segments, metadata, source_name="osm")

    assert written > 0

    with owner_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM road_segments")
        row = cur.fetchone()
        assert row is not None
        assert row[0] > 0

        cur.execute("SELECT name, last_ingested_at FROM data_sources WHERE name = 'osm'")
        ds = cur.fetchone()
        assert ds is not None
        assert ds[0] == "osm"
        assert ds[1] is not None
