"""City-scoped tile functions — Phase 4b Task 3.6.

Phase 4b makes every read endpoint city-scoped. The four pg_tileserv
tile functions (``road_segments_tile_t`` /
``road_segments_tile_t_rows`` and ``road_segments_tile_delta`` /
``road_segments_tile_delta_rows``) gain a required ``city_slug``
argument that filters segments by the matching ``cities.id``.

Two architectural choices anchored here (both consistent with the
Phase 4b plan's parenthetical "or the equivalent for the chosen tile
server"):

1. **URL shape stays pg_tileserv's auto-published shape**
   ``/tiles/{function_name}/{z}/{x}/{y}.pbf`` rather than a
   FastAPI-proxied ``/tiles/{city_slug}/segments/...``. pg_tileserv
   exposes function arguments as query-string parameters, so
   ``?city_slug=phoenix`` slots in alongside ``?t=...`` /
   ``?run_a=...&run_b=...``.

2. **Unknown slug → empty MVT, never 500.** Inside the SQL,
   ``city_slug`` is resolved by subquery on the ``cities`` table; if
   the slug is unknown the subquery returns NULL and
   ``rs.city_id = NULL`` filters out every row. The bytes wrapper then
   emits an empty layer envelope, which the frontend handles silently.
   This matches the plan's verification ("a tile request for a slug
   outside its bbox returns an empty MVT, not a 500").

Both tile-function families are exercised in parallel; the test names
mirror the existing ``test_tile_t.py`` / ``test_tile_delta.py``
structure so the city-scoping deltas are easy to diff in review.

Skipped without ``DATABASE_URL`` — per
``tests/README.md``'s memory note, the autouse TRUNCATE fixtures wipe
live data; run these BEFORE live ingests or against a separate test
database.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from shapely import wkb
from shapely.geometry import LineString

from scoring.environmental.glare import GlareScorer
from scoring.run import ScoringRun, ScoringRunConfig

pytestmark = pytest.mark.integration


TILE_FN_T_BYTES = "public.road_segments_tile_t"
TILE_FN_T_ROWS = "public.road_segments_tile_t_rows"
TILE_FN_DELTA_BYTES = "public.road_segments_tile_delta"
TILE_FN_DELTA_ROWS = "public.road_segments_tile_delta_rows"

# A z14 tile near (-71.105, 42.370) covers the Cambridge fixture segments
# used by sibling test modules. Same lat/lon constants so visually
# obvious that we're exercising the same map area, only with a city
# filter layered on top.
_FIX_LON = -71.105
_FIX_LAT = 42.370

TEMPORAL_SAMPLES = tuple(datetime(2025, 6, 21, h, 0, tzinfo=UTC) for h in range(24))
_RUN_TS = datetime(2025, 6, 21, 16, 0, tzinfo=UTC)


def _lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


# ---------------------------------------------------------------------------
# Auxiliary fixtures: a second city + a segment scoped to each
# ---------------------------------------------------------------------------


@pytest.fixture
def phoenix_city_id(owner_conn: psycopg.Connection[Any]) -> Any:
    """Ensure a ``phoenix`` row exists in the ``cities`` table.

    The migrated_db fixture only seeds ``cambridge``. ``make seed-cities``
    is what brings phoenix + the three other curated additions in; this
    test-local fixture upserts phoenix directly so the tests run cleanly
    against a freshly migrated DB without depending on the seeder
    having been invoked first.
    """
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cities (slug, name, bbox, default_zoom, timezone)
            VALUES (
                'phoenix',
                'Phoenix, AZ',
                ST_MakeEnvelope(-112.32, 33.29, -111.93, 33.92, 4326),
                11,
                'America/Phoenix'
            )
            ON CONFLICT (slug) DO UPDATE SET updated_at = now()
            RETURNING id
            """
        )
        row = cur.fetchone()
        assert row is not None
        city_id = row[0]
    owner_conn.commit()
    return city_id


@pytest.fixture
def seeded_cambridge_run(
    owner_conn: psycopg.Connection[Any],
    database_url: str,
    cambridge_city_id: Any,
    phoenix_city_id: Any,
) -> UUID:
    """One Cambridge segment + one Phoenix segment + a cambridge-only run.

    The Phoenix segment sits *inside* the Cambridge tile bbox on purpose:
    when the tile function is given ``city_slug='cambridge'`` it must
    filter the Phoenix row out by ``city_id``, not by geometry. That's
    the city-scoping invariant the migration delivers; geometry overlap
    is irrelevant to it.

    Returns the cambridge segment id.
    """
    geom_cambridge = LineString([(-71.110, 42.370), (-71.100, 42.370)])
    # Same coordinates so the segment is unambiguously inside the same
    # tile envelope; differs only in city_id.
    geom_phoenix_in_cambridge_bbox = LineString(
        [(-71.108, 42.371), (-71.102, 42.371)]
    )
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments CASCADE")
        cur.execute(
            """
            INSERT INTO road_segments (osm_way_id, geometry, attrs, city_id)
            VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), %s::jsonb, %s)
            RETURNING id
            """,
            (888_011, wkb.dumps(geom_cambridge), '{"highway": "primary"}', cambridge_city_id),
        )
        row = cur.fetchone()
        assert row is not None
        seg_id: UUID = row[0]

        # Phoenix segment that *would* be visible under the cambridge
        # tile envelope if there were no city filter. The fixture exists
        # to prove the filter actually filters.
        cur.execute(
            """
            INSERT INTO road_segments (osm_way_id, geometry, attrs, city_id)
            VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), %s::jsonb, %s)
            """,
            (
                888_012,
                wkb.dumps(geom_phoenix_in_cambridge_bbox),
                '{"highway": "primary"}',
                phoenix_city_id,
            ),
        )
    owner_conn.commit()

    ScoringRun(
        config=ScoringRunConfig(
            temporal_samples=TEMPORAL_SAMPLES,
            osm_snapshot_date=date(2026, 5, 13),
            city_id=cambridge_city_id,
        ),
        scorers=[GlareScorer()],
        database_url=database_url,
    ).execute()
    return seg_id


# ---------------------------------------------------------------------------
# Function signatures: city_slug is required, not optional
# ---------------------------------------------------------------------------


class TestTileFunctionSignatures:
    """Both tile-function families must accept a ``city_slug`` argument.

    Introspect ``pg_get_function_arguments`` rather than try/except'ing
    the call: this is the contract pg_tileserv reads to publish the
    query-string parameters. If the signature is wrong, the URL the
    frontend builds won't match the function pg_tileserv resolves.
    """

    @pytest.mark.parametrize(
        "fn",
        [
            "road_segments_tile_t",
            "road_segments_tile_t_rows",
            "road_segments_tile_delta",
            "road_segments_tile_delta_rows",
        ],
    )
    def test_function_argument_includes_city_slug(
        self, fn: str, owner_conn: psycopg.Connection[Any]
    ) -> None:
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_get_function_arguments(p.oid)
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public' AND p.proname = %s
                """,
                (fn,),
            )
            rows = cur.fetchall()
        assert rows, f"function public.{fn} does not exist"
        # pg_get_function_arguments lists every overload; assert at
        # least one carries city_slug so we don't lock the migration
        # into a single shape (e.g., a transitional dual-signature
        # period during the refactor).
        sigs = [r[0] for r in rows]
        assert any("city_slug" in sig for sig in sigs), (
            f"public.{fn} signatures missing city_slug; got {sigs!r}"
        )


# ---------------------------------------------------------------------------
# road_segments_tile_t — city-scoped behavior
# ---------------------------------------------------------------------------


class TestTileTCityScope:
    def test_cambridge_slug_returns_only_cambridge_rows(
        self,
        seeded_cambridge_run: UUID,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        x, y = _lonlat_to_tile(_FIX_LON, _FIX_LAT, 14)
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {TILE_FN_T_ROWS}(%s, %s, %s, %s::timestamptz, %s)",
                (14, x, y, _RUN_TS.isoformat(), "cambridge"),
            )
            ids = {r[0] for r in cur.fetchall()}
        # Only the cambridge-tagged segment must come back — the phoenix
        # segment lives at coords inside the same envelope and would
        # otherwise pass the geometry filter.
        assert ids == {seeded_cambridge_run}, (
            f"expected only the cambridge segment; got {ids}"
        )

    def test_phoenix_slug_returns_empty(
        self,
        seeded_cambridge_run: UUID,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        """The phoenix segment exists but has no scoring run yet — and
        in any case lives outside the Cambridge tile. Asking for
        ``phoenix`` over the Cambridge bbox must return zero rows
        without erroring."""
        del seeded_cambridge_run
        x, y = _lonlat_to_tile(_FIX_LON, _FIX_LAT, 14)
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {TILE_FN_T_ROWS}(%s, %s, %s, %s::timestamptz, %s)",
                (14, x, y, _RUN_TS.isoformat(), "phoenix"),
            )
            row = cur.fetchone()
        assert row is not None
        assert int(row[0]) == 0

    def test_unknown_slug_returns_empty_mvt_not_500(
        self,
        seeded_cambridge_run: UUID,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        """Plan acceptance: 'a tile request for a slug outside its bbox
        returns an empty MVT, not a 500'. The bytes wrapper is the
        public face of the function — exercise it directly."""
        del seeded_cambridge_run
        x, y = _lonlat_to_tile(_FIX_LON, _FIX_LAT, 14)
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT {TILE_FN_T_BYTES}(%s, %s, %s, %s::timestamptz, %s)",
                (14, x, y, _RUN_TS.isoformat(), "does-not-exist"),
            )
            row = cur.fetchone()
        assert row is not None
        mvt = row[0]
        # Either NULL or a very small empty-layer envelope is acceptable;
        # crucially, the query did not raise.
        assert mvt is None or len(mvt) <= 64, (
            f"unknown-slug MVT must be empty/small; got {len(mvt)} bytes"
        )

    def test_cambridge_bytes_wrapper_non_empty(
        self,
        seeded_cambridge_run: UUID,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        """Sanity: with a real city + a scoring run, the bytes wrapper
        still emits a non-empty MVT after the city filter is added."""
        del seeded_cambridge_run
        x, y = _lonlat_to_tile(_FIX_LON, _FIX_LAT, 14)
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT {TILE_FN_T_BYTES}(%s, %s, %s, %s::timestamptz, %s)",
                (14, x, y, _RUN_TS.isoformat(), "cambridge"),
            )
            row = cur.fetchone()
        assert row is not None
        mvt = row[0]
        assert isinstance(mvt, (bytes, bytearray, memoryview))
        assert len(mvt) > 0


# ---------------------------------------------------------------------------
# road_segments_tile_delta — city-scoped behavior
# ---------------------------------------------------------------------------


def _insert_delta_run(
    cur: psycopg.Cursor[Any], ts: datetime, *, city_id: Any
) -> UUID:
    cur.execute(
        """
        INSERT INTO scoring_runs (
            scoring_run_timestamp,
            perception_model_version,
            osm_snapshot_date,
            imagery_capture_window,
            propagation_algorithm_version,
            notes,
            city_id
        )
        VALUES (%s, %s, %s, daterange(%s, %s, '[)'), %s, %s, %s)
        RETURNING id
        """,
        (
            ts,
            "stand-in-onnx-0.1.0",
            date(2026, 5, 1),
            date(2025, 11, 1),
            date(2026, 5, 1),
            "pagerank-diffusion-0.1.0",
            "task 3.6 city-scope delta",
            city_id,
        ),
    )
    row = cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


def _insert_delta_score(
    cur: psycopg.Cursor[Any],
    *,
    segment_id: UUID,
    run_id: UUID,
    run_ts: datetime,
    city_id: Any,
    composite: float,
    uplift: float,
) -> None:
    cur.execute(
        """
        INSERT INTO segment_scores (
            segment_id,
            composite_risk,
            sub_score_lane_marking,
            sub_score_glare,
            sub_score_junction_complexity,
            sub_score_historical,
            confidence,
            scoring_run_id,
            scoring_run_timestamp,
            perception_model_version,
            osm_snapshot_date,
            imagery_capture_window,
            propagation_algorithm_version,
            propagation_uplift,
            is_stub_lane_marking,
            is_stub_glare,
            is_stub_junction_complexity,
            is_stub_historical,
            city_id
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, daterange(%s, %s, '[)'),
            %s, %s,
            false, false, false, false,
            %s
        )
        """,
        (
            segment_id,
            composite,
            0.2,
            0.1,
            0.0,
            0.0,
            0.8,
            run_id,
            run_ts,
            "stand-in-onnx-0.1.0",
            date(2026, 5, 1),
            date(2025, 11, 1),
            date(2026, 5, 1),
            "pagerank-diffusion-0.1.0",
            uplift,
            city_id,
        ),
    )


@pytest.fixture
def seeded_delta_with_two_cities(
    owner_conn: psycopg.Connection[Any],
    cambridge_city_id: Any,
    phoenix_city_id: Any,
) -> tuple[UUID, UUID, UUID, UUID]:
    """Two runs at the same noon hour for cambridge, plus a parallel pair
    for phoenix, both sharing one segment per city sitting inside the
    Cambridge tile envelope. Returns ``(cambridge_run_a, cambridge_run_b,
    cambridge_segment_id, phoenix_segment_id)``."""
    geom = LineString([(-71.108, 42.371), (-71.102, 42.371)])
    ts_a = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    ts_b = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments CASCADE")

        cambridge_seg_id_row = cur.execute(
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
        ).fetchone()
        assert cambridge_seg_id_row is not None
        cambridge_seg_id = UUID(str(cambridge_seg_id_row[0]))

        # Phoenix segment overlapping the same tile coords.
        phoenix_seg_id_row = cur.execute(
            """
            INSERT INTO road_segments (osm_way_id, geometry, attrs, city_id)
            VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), %s::jsonb, %s)
            RETURNING id
            """,
            (
                930_002,
                wkb.dumps(geom),
                '{"highway": "primary"}',
                phoenix_city_id,
            ),
        ).fetchone()
        assert phoenix_seg_id_row is not None
        phoenix_seg_id = UUID(str(phoenix_seg_id_row[0]))

        cambridge_run_a = _insert_delta_run(cur, ts_a, city_id=cambridge_city_id)
        cambridge_run_b = _insert_delta_run(cur, ts_b, city_id=cambridge_city_id)
        phoenix_run_a = _insert_delta_run(cur, ts_a, city_id=phoenix_city_id)
        phoenix_run_b = _insert_delta_run(cur, ts_b, city_id=phoenix_city_id)

        _insert_delta_score(
            cur,
            segment_id=cambridge_seg_id,
            run_id=cambridge_run_a,
            run_ts=ts_a,
            city_id=cambridge_city_id,
            composite=0.30,
            uplift=0.05,
        )
        _insert_delta_score(
            cur,
            segment_id=cambridge_seg_id,
            run_id=cambridge_run_b,
            run_ts=ts_b,
            city_id=cambridge_city_id,
            composite=0.40,
            uplift=0.08,
        )
        _insert_delta_score(
            cur,
            segment_id=phoenix_seg_id,
            run_id=phoenix_run_a,
            run_ts=ts_a,
            city_id=phoenix_city_id,
            composite=0.50,
            uplift=0.10,
        )
        _insert_delta_score(
            cur,
            segment_id=phoenix_seg_id,
            run_id=phoenix_run_b,
            run_ts=ts_b,
            city_id=phoenix_city_id,
            composite=0.55,
            uplift=0.12,
        )
    owner_conn.commit()
    return cambridge_run_a, cambridge_run_b, cambridge_seg_id, phoenix_seg_id


class TestTileDeltaCityScope:
    def test_cambridge_slug_returns_only_cambridge_segment(
        self,
        seeded_delta_with_two_cities: tuple[UUID, UUID, UUID, UUID],
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        run_a, run_b, cambridge_seg, _phoenix_seg = seeded_delta_with_two_cities
        x, y = _lonlat_to_tile(_FIX_LON, _FIX_LAT, 14)
        ts = datetime(2026, 5, 15, 12, 0, tzinfo=UTC).isoformat()
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {TILE_FN_DELTA_ROWS}("
                "%s, %s, %s, %s, %s, %s::timestamptz, %s)",
                (14, x, y, run_a, run_b, ts, "cambridge"),
            )
            ids = {r[0] for r in cur.fetchall()}
        assert ids == {cambridge_seg}, (
            f"expected only the cambridge segment; got {ids}"
        )

    def test_unknown_slug_returns_empty_mvt_not_500(
        self,
        seeded_delta_with_two_cities: tuple[UUID, UUID, UUID, UUID],
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        run_a, run_b, *_ = seeded_delta_with_two_cities
        x, y = _lonlat_to_tile(_FIX_LON, _FIX_LAT, 14)
        ts = datetime(2026, 5, 15, 12, 0, tzinfo=UTC).isoformat()
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT {TILE_FN_DELTA_BYTES}("
                "%s, %s, %s, %s, %s, %s::timestamptz, %s)",
                (14, x, y, run_a, run_b, ts, "does-not-exist"),
            )
            row = cur.fetchone()
        assert row is not None
        mvt = row[0]
        assert mvt is None or len(mvt) <= 64

    def test_bytes_wrapper_non_empty_for_cambridge(
        self,
        seeded_delta_with_two_cities: tuple[UUID, UUID, UUID, UUID],
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        run_a, run_b, *_ = seeded_delta_with_two_cities
        x, y = _lonlat_to_tile(_FIX_LON, _FIX_LAT, 14)
        ts = datetime(2026, 5, 15, 12, 0, tzinfo=UTC).isoformat()
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT {TILE_FN_DELTA_BYTES}("
                "%s, %s, %s, %s, %s, %s::timestamptz, %s)",
                (14, x, y, run_a, run_b, ts, "cambridge"),
            )
            row = cur.fetchone()
        assert row is not None
        mvt = row[0]
        assert isinstance(mvt, (bytes, bytearray, memoryview))
        assert len(mvt) > 0


# ---------------------------------------------------------------------------
# App-role grants still cover the new signatures
# ---------------------------------------------------------------------------


class TestAppRoleCanExecuteCityScopedSignatures:
    """The pg_tileserv service account connects as the app role; if the
    migration drops + recreates a function it must regrant EXECUTE so
    the production tile URL keeps returning 200."""

    def test_app_role_executes_tile_t_with_city_slug(
        self,
        seeded_cambridge_run: UUID,
        app_conn: psycopg.Connection[Any],
    ) -> None:
        del seeded_cambridge_run
        x, y = _lonlat_to_tile(_FIX_LON, _FIX_LAT, 14)
        with app_conn.cursor() as cur:
            cur.execute(
                f"SELECT {TILE_FN_T_BYTES}(%s, %s, %s, %s::timestamptz, %s)",
                (14, x, y, _RUN_TS.isoformat(), "cambridge"),
            )
            row = cur.fetchone()
        assert row is not None

    def test_app_role_executes_tile_delta_with_city_slug(
        self,
        seeded_delta_with_two_cities: tuple[UUID, UUID, UUID, UUID],
        app_conn: psycopg.Connection[Any],
    ) -> None:
        run_a, run_b, *_ = seeded_delta_with_two_cities
        x, y = _lonlat_to_tile(_FIX_LON, _FIX_LAT, 14)
        ts = datetime(2026, 5, 15, 12, 0, tzinfo=UTC).isoformat()
        with app_conn.cursor() as cur:
            cur.execute(
                f"SELECT {TILE_FN_DELTA_BYTES}("
                "%s, %s, %s, %s, %s, %s::timestamptz, %s)",
                (14, x, y, run_a, run_b, ts, "cambridge"),
            )
            row = cur.fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# Defensive: a random UUID still produces no rows (regression guard for
# the unknown-slug path; documents that the function never raises).
# ---------------------------------------------------------------------------


class TestUnknownSlugDefensive:
    def test_random_uuid_like_slug_returns_zero_rows(
        self,
        seeded_cambridge_run: UUID,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        del seeded_cambridge_run
        x, y = _lonlat_to_tile(_FIX_LON, _FIX_LAT, 14)
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {TILE_FN_T_ROWS}("
                "%s, %s, %s, %s::timestamptz, %s)",
                (14, x, y, _RUN_TS.isoformat(), str(uuid4())),
            )
            row = cur.fetchone()
        assert row is not None
        assert int(row[0]) == 0
