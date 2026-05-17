"""``GET /admin/freshness`` per-city extension — Phase 4b Task 3.5 (TDD red).

The Phase 1-4 freshness endpoint returned a flat ``sources`` list with
one row per global source name (osm, imagery, incidents, plus the
compute / model sources). Phase 4b makes ingestion city-scoped, so
freshness becomes city-scoped too: the response gains a new ``cities``
field keyed by slug, with one timestamped entry per per-city source.

Contract (locked by these tests):

- The existing ``sources`` list stays present (backwards-compatible) —
  still reports global compute/model sources (solar_position,
  perception_model, propagation_algorithm).
- New ``cities`` field is a ``dict[slug, dict[source, timestamp|None]]``.
- Per-city sources surfaced from the spatial tables (now city-scoped
  via the migration-0017 city_id columns):
    * ``osm`` — max(road_segments.created_at) per city
    * ``imagery`` — max(segment_imagery.ingested_at) per city
    * ``incidents`` — max(incidents.ingested_at) per city
    * ``scoring_run`` — max(scoring_runs.scoring_run_timestamp) per city
- Every slug in the ``cities`` table appears in the response, including
  slugs whose tables are empty — those rows carry all-null values so
  the frontend can render "no data yet" without ambiguity.

Why derive per-city freshness from the spatial tables rather than
adding city_id to data_sources: data_sources tracks the "source kind"
metadata (provider, license, ADR ref), which is global. The
per-(source, city) ingestion timestamps live naturally on the rows
themselves. Adding city_id to data_sources would double-store the
information; deriving from the tables keeps a single source of truth.

Cambridge is the seed-bearing city (we insert one road_segment for
it in the fixture); phoenix is the seeded-but-empty city, exercising
the "valid slug, no data" branch.

Integration tests — requires a running, migrated Postgres.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import psycopg
import pytest
from httpx import AsyncClient
from shapely import wkb
from shapely.geometry import LineString

from ingestion.seed_cities import seed_cities

pytestmark = pytest.mark.integration


_EXPECTED_SLUGS: frozenset[str] = frozenset(
    {"cambridge", "phoenix", "san-francisco", "austin", "los-angeles"}
)

_PER_CITY_SOURCES: frozenset[str] = frozenset({"osm", "imagery", "incidents", "scoring_run"})


# --- Fixtures -------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _seeded_cities(database_url: str) -> None:
    """Ensure all five YAML-configured cities are in the table.

    Migration 0017 seeds only cambridge; the other four come from
    ``make seed-cities``. The per-city freshness response must list
    *every* city in the table, so the "valid slug but no data" case
    is testable.
    """
    seed_cities(database_url)


@pytest.fixture(autouse=True)
def _clean_data_tables(owner_conn: psycopg.Connection[Any]) -> None:
    """Reset to a known empty state before each test.

    The freshness endpoint reads max() aggregates; a leftover row from
    another test would smear the assertion. Cities are NOT truncated —
    they're seeded once at module load and exist for the lifetime of
    the session.
    """
    with owner_conn.cursor() as cur:
        cur.execute(
            "TRUNCATE segment_scores, scoring_runs, segment_imagery, "
            "incidents, road_segments CASCADE"
        )
    owner_conn.commit()


@pytest.fixture
def seed_cambridge_road_segment(
    owner_conn: psycopg.Connection[Any], cambridge_city_id: Any
) -> UUID:
    """Insert one road_segment for cambridge. Returns its UUID.

    The freshness endpoint's ``osm`` field for cambridge will surface
    this row's ``created_at`` as the latest OSM ingestion timestamp.
    """
    geom = LineString([(-71.110, 42.370), (-71.100, 42.370)])
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO road_segments (osm_way_id, geometry, attrs, city_id)
            VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), %s::jsonb, %s)
            RETURNING id
            """,
            (
                930_001,
                wkb.dumps(geom),
                '{"highway": "primary"}',
                cambridge_city_id,
            ),
        )
        row = cur.fetchone()
        assert row is not None
    owner_conn.commit()
    return UUID(str(row[0]))


@pytest.fixture
def seed_cambridge_scoring_run(
    owner_conn: psycopg.Connection[Any], cambridge_city_id: Any
) -> datetime:
    """Insert one cambridge scoring_run. Returns its timestamp."""
    run_ts = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO scoring_runs (
                scoring_run_timestamp, perception_model_version,
                osm_snapshot_date, imagery_capture_window,
                propagation_algorithm_version, notes, city_id
            ) VALUES (
                %s, 'lane-marking-standin-deadbeef', '2026-05-13',
                daterange('2025-11-01', '2026-05-01', '[)'),
                'pagerank-diffusion-0.1.0', 'task 3.5 freshness', %s
            )
            """,
            (run_ts, cambridge_city_id),
        )
    owner_conn.commit()
    return run_ts


@pytest.fixture
def seed_cambridge_imagery(
    owner_conn: psycopg.Connection[Any],
    cambridge_city_id: Any,
    seed_cambridge_road_segment: UUID,
) -> None:
    """Insert one segment_imagery row for cambridge."""
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO segment_imagery (
                segment_id, provider, provider_image_id, sample_index,
                capture_date, heading_deg, camera_params, object_key,
                city_id
            ) VALUES (
                %s, 'mapillary', 'task-3.5-fixture', 0,
                %s, 90.0, '{}'::jsonb, 'mapillary/task-3.5-fixture.jpg',
                %s
            )
            """,
            (seed_cambridge_road_segment, date(2025, 7, 15), cambridge_city_id),
        )
    owner_conn.commit()


# --- Tests ----------------------------------------------------------------


class TestFreshnessPerCityShape:
    """Response envelope and per-city map structure."""

    @pytest.mark.asyncio
    async def test_response_carries_cities_keyed_by_slug(self, api_client: AsyncClient) -> None:
        """The new ``cities`` field is a dict (not a list) keyed by slug.

        Keyed-by-slug (rather than ``[{slug: ..., osm: ...}, ...]``)
        matches the plan wording ``{[slug]: {source: latest_timestamp,
        ...}}`` and makes "find this city's freshness" an O(1) lookup
        on the frontend.
        """
        resp = await api_client.get("/admin/freshness")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "cities" in body, (
            f"freshness response must carry a 'cities' field; got keys {sorted(body.keys())}"
        )
        cities = body["cities"]
        assert isinstance(cities, dict), (
            f"'cities' must be a dict keyed by slug; got {type(cities).__name__}"
        )

    @pytest.mark.asyncio
    async def test_every_seeded_slug_appears_even_with_no_data(
        self, api_client: AsyncClient
    ) -> None:
        """Phoenix has a row in ``cities`` but no segments / runs /
        imagery / incidents — it still appears, with all-null values."""
        body = (await api_client.get("/admin/freshness")).json()
        slugs = set(body["cities"].keys())
        missing = _EXPECTED_SLUGS - slugs
        assert not missing, f"per-city freshness missing seeded slugs: {sorted(missing)}"

    @pytest.mark.asyncio
    async def test_each_city_entry_has_every_per_city_source_key(
        self, api_client: AsyncClient
    ) -> None:
        """Every city's freshness map has the same set of source keys.

        A consistent key set across cities lets the frontend iterate
        the source order once (e.g., in a table) rather than computing
        a union per city.
        """
        body = (await api_client.get("/admin/freshness")).json()
        for slug, entry in body["cities"].items():
            assert isinstance(entry, dict), (
                f"{slug}: freshness entry must be a dict; got {type(entry).__name__}"
            )
            missing = _PER_CITY_SOURCES - entry.keys()
            assert not missing, (
                f"{slug}: per-city freshness missing source keys {sorted(missing)}; "
                f"got {sorted(entry.keys())}"
            )

    @pytest.mark.asyncio
    async def test_unseeded_city_has_all_null_per_city_sources(
        self, api_client: AsyncClient
    ) -> None:
        """Phoenix is in cities but has no data — every value is null.

        This is the explicit "valid slug, no data" branch the plan
        Task 3.5 description names. The contract is null-not-missing
        so the frontend can render "no data yet" without ambiguity
        between absence and presence-of-null.
        """
        body = (await api_client.get("/admin/freshness")).json()
        phoenix = body["cities"]["phoenix"]
        for source in _PER_CITY_SOURCES:
            assert phoenix[source] is None, (
                f"phoenix has no fixture data; expected {source} to be null, "
                f"got {phoenix[source]!r}"
            )


class TestFreshnessPerCityValues:
    """Values surface from the right underlying queries."""

    @pytest.mark.asyncio
    async def test_cambridge_osm_freshness_set_after_road_segment_inserted(
        self,
        seed_cambridge_road_segment: UUID,
        api_client: AsyncClient,
    ) -> None:
        """A road_segment row makes cambridge.osm non-null."""
        del seed_cambridge_road_segment
        body = (await api_client.get("/admin/freshness")).json()
        cambridge = body["cities"]["cambridge"]
        assert cambridge["osm"] is not None, (
            "after inserting a cambridge road_segment, osm freshness must be populated"
        )

    @pytest.mark.asyncio
    async def test_cambridge_imagery_freshness_set_after_imagery_inserted(
        self,
        seed_cambridge_imagery: None,
        api_client: AsyncClient,
    ) -> None:
        """A segment_imagery row makes cambridge.imagery non-null."""
        del seed_cambridge_imagery
        body = (await api_client.get("/admin/freshness")).json()
        cambridge = body["cities"]["cambridge"]
        assert cambridge["imagery"] is not None, (
            "after inserting cambridge imagery, imagery freshness must be populated"
        )

    @pytest.mark.asyncio
    async def test_cambridge_scoring_run_freshness_set_after_run_inserted(
        self,
        seed_cambridge_scoring_run: datetime,
        api_client: AsyncClient,
    ) -> None:
        """A scoring_runs row makes cambridge.scoring_run match its
        ``scoring_run_timestamp`` exactly — the freshness query reads
        the column directly, no aggregation other than ``max()`` over
        a single row."""
        run_ts = seed_cambridge_scoring_run
        body = (await api_client.get("/admin/freshness")).json()
        cambridge = body["cities"]["cambridge"]
        assert cambridge["scoring_run"] is not None
        # Timestamp round-trips through JSON as an ISO-8601 string.
        returned = datetime.fromisoformat(cambridge["scoring_run"].replace("Z", "+00:00"))
        assert returned == run_ts, (
            f"scoring_run freshness should equal the run's timestamp; "
            f"expected {run_ts.isoformat()}, got {returned.isoformat()}"
        )


class TestFreshnessBackwardsCompatible:
    """The pre-Phase-4b shape stays — ``sources`` and ``server_time``
    are still present after the per-city extension lands."""

    @pytest.mark.asyncio
    async def test_existing_sources_field_still_present(
        self, api_client: AsyncClient, seed_data_sources: None
    ) -> None:
        del seed_data_sources
        body = (await api_client.get("/admin/freshness")).json()
        assert "sources" in body
        assert isinstance(body["sources"], list)
        # Same 6 global sources the Phase 4 tests assert on.
        names = {entry["name"] for entry in body["sources"]}
        assert {
            "osm",
            "imagery",
            "solar_position",
            "perception_model",
            "incidents",
            "propagation_algorithm",
        } <= names

    @pytest.mark.asyncio
    async def test_server_time_still_present(
        self, api_client: AsyncClient, seed_data_sources: None
    ) -> None:
        del seed_data_sources
        body = (await api_client.get("/admin/freshness")).json()
        assert "server_time" in body
