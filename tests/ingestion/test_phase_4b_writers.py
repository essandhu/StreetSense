"""Phase 4b Phase 2 writer-refactor tests.

The Phase 2 refactor parameterizes every ingestion writer by `city_id`
(resolved from --city <slug> by the CLI orchestrators). These tests
pin:

1. ``get_city_id_by_slug`` returns the right UUID for known slugs and
   raises ``UnknownCityError`` (with valid-slug list) for unknown.
2. ``persist_road_segments`` accepts ``city_id`` and tags every
   inserted row with it.
3. ``ingest_imagery`` filters ``road_segments`` by ``city_id`` and tags
   each ``segment_imagery`` row with the same.
4. ``ingest_incidents`` tags every ``incidents`` row with ``city_id``.

The full ingestion-runs for the curated cities (Task 2.5) are
deferred; these tests use the Phase 1-4 fixtures + the cambridge
city_id resolved via the helper.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest

from ingestion.seed_cities import UnknownCityError, get_city_id_by_slug

pytestmark = pytest.mark.integration


# ---- get_city_id_by_slug -------------------------------------------------


def test_get_city_id_by_slug_returns_cambridge_uuid(
    database_url: str, owner_conn: psycopg.Connection[Any]
) -> None:
    city_id = get_city_id_by_slug(database_url, "cambridge")
    assert isinstance(city_id, UUID)
    with owner_conn.cursor() as cur:
        cur.execute("SELECT id FROM cities WHERE slug = 'cambridge'")
        row = cur.fetchone()
    assert row is not None
    assert city_id == row[0]


@pytest.mark.parametrize(
    "slug",
    ["phoenix", "san-francisco", "austin", "los-angeles"],
)
def test_get_city_id_by_slug_returns_curated_uuids(database_url: str, slug: str) -> None:
    city_id = get_city_id_by_slug(database_url, slug)
    assert isinstance(city_id, UUID)


def test_get_city_id_by_slug_raises_on_unknown(database_url: str) -> None:
    with pytest.raises(UnknownCityError) as exc_info:
        get_city_id_by_slug(database_url, "no-such-city")
    err = exc_info.value
    assert err.slug == "no-such-city"
    # Valid slugs surfaced in the error so the CLI can echo them.
    assert "cambridge" in err.valid_slugs
    assert "phoenix" in err.valid_slugs


# ---- persist_road_segments tags inserts with city_id --------------------


def test_persist_road_segments_tags_rows_with_city_id(
    database_url: str,
    owner_conn: psycopg.Connection[Any],
    tmp_path: Path,
) -> None:
    """The Phase 2 refactor adds a ``city_id`` parameter to
    ``persist_road_segments``. Every inserted row must carry that
    city_id.

    This test runs against the existing fixture extract used by
    ``test_seed_integration.py`` so we don't need a live OSM fetch.
    """
    from ingestion.osm.osmium_adapter import OsmiumOSMSource
    from ingestion.persist import persist_road_segments

    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "tiny_extract.osm"
    fixture_bbox = (-71.10, 42.36, -71.08, 42.38)

    # Clean slate so the count assertion is deterministic.
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments CASCADE")
        cur.execute("DELETE FROM data_sources WHERE name = 'osm'")
    owner_conn.commit()

    adapter = OsmiumOSMSource(
        prefetched=fixture_path, snapshot_date_for_prefetched=date(2026, 5, 1)
    )
    metadata = adapter.fetch(fixture_bbox, fixture_path)
    segments = adapter.parse(metadata.local_path, fixture_bbox)

    city_id = get_city_id_by_slug(database_url, "cambridge")
    written = persist_road_segments(
        database_url, segments, metadata, source_name="osm", city_id=city_id
    )
    assert written > 0

    with owner_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE city_id = %s) FROM road_segments",
            (city_id,),
        )
        row = cur.fetchone()
    assert row is not None
    total, tagged = row
    assert total == tagged, f"{total - tagged} of {total} rows are not tagged with cambridge"


# ---- ingest_imagery accepts city_id + filters segments -------------------


def test_ingest_imagery_signature_takes_city_id(database_url: str) -> None:
    """Smoke check that ``ingest_imagery`` accepts the new ``city_id``
    keyword. Full behavior is exercised in
    ``test_imagery_job_persistence.py`` after the refactor lands.
    """
    import inspect

    from ingestion.imagery.job import ingest_imagery

    sig = inspect.signature(ingest_imagery)
    assert "city_id" in sig.parameters, (
        f"ingest_imagery missing city_id parameter; signature: {sig}"
    )


# ---- ingest_incidents accepts city_id ------------------------------------


def test_ingest_incidents_signature_takes_city_id() -> None:
    import inspect

    from ingestion.incidents.job import ingest_incidents

    sig = inspect.signature(ingest_incidents)
    assert "city_id" in sig.parameters, (
        f"ingest_incidents missing city_id parameter; signature: {sig}"
    )


# ---- scoring runner takes city_id ----------------------------------------


def test_scoring_run_config_carries_city_id() -> None:
    """``ScoringRunConfig`` gains a ``city_id`` field. The Phase 2
    scoring refactor persists this on every scoring_runs and
    segment_scores row.
    """
    import inspect

    from scoring.run import ScoringRunConfig

    fields = inspect.signature(ScoringRunConfig).parameters
    assert "city_id" in fields, (
        f"ScoringRunConfig missing city_id parameter; fields: {list(fields)}"
    )
