"""Delta tile function — Phase 5 Task 2.6.

Verifies the new ``public.road_segments_tile_delta`` MVT pipeline that
paints per-segment ``composite_delta`` (and the decomposition into
``local_contribution_delta`` + ``propagation_uplift_delta``) GPU-side
between two scoring runs.

Decision documented in
``conductor/tracks/phase-5-delta-deployment/index.md``: a **new**
tile function (``road_segments_tile_delta`` / ``..._rows``) rather
than parameterizing ``road_segments_tile_t``. Reasoning:

* Delta tiles emit a different attribute set (deltas, not single-run
  scores) — different shape deserves a different layer name.
* pg_tileserv publishes each function as its own tile-source URL;
  a separate layer maps cleanly to the planned
  ``frontend/src/components/map/deltaLayer.ts`` (Task 3.4).
* No risk of the single-run hot path picking up extra parameter-
  dispatch overhead.

These tests follow the same DB-only / pg_tileserv HTTP split as
``test_tile_t.py``: rows-function shape and bytes-wrapper non-emptiness
hit Postgres directly; the HTTP smoke test against ``pg_tileserv``
skips when the container isn't reachable.

Skipped without ``DATABASE_URL`` — per the memory note in
``tests/README.md``, the autouse TRUNCATE fixture wipes live data, so
run these BEFORE live ingests or against a separate test database.
"""

from __future__ import annotations

import math
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import psycopg
import pytest
from shapely import wkb
from shapely.geometry import LineString

pytestmark = pytest.mark.integration


TILE_FN_BYTES = "public.road_segments_tile_delta"
TILE_FN_ROWS = "public.road_segments_tile_delta_rows"

# Fixture segments live inside this Cambridge-ish bounding box so a
# z14 tile centered near (-71.105, 42.370) covers them.
_FIXTURE_LON = -71.105
_FIXTURE_LAT = 42.370

# Two runs at the same noon hour, a week apart — matches the weekly-
# cron cadence and the Task 2.3 / 2.4 fixtures.
_HOUR = 12
_RUN_A_TS = datetime(2026, 5, 8, _HOUR, 0, 0, tzinfo=UTC)
_RUN_B_TS = datetime(2026, 5, 15, _HOUR, 0, 0, tzinfo=UTC)

_PERCEPTION_VERSION = "stand-in-onnx-0.1.0"
_PROPAGATION_VERSION = "pagerank-diffusion-0.1.0"
_OSM_SNAPSHOT_DATE = date(2026, 5, 1)
_IMAGERY_START = date(2025, 11, 1)
_IMAGERY_END = date(2026, 5, 1)


def _lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _insert_scoring_run(
    cur: psycopg.Cursor[Any], run_timestamp: datetime, *, notes: str, city_id: Any
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
        VALUES (
            %s, %s, %s, daterange(%s, %s, '[)'), %s, %s, %s
        )
        RETURNING id
        """,
        (
            run_timestamp,
            _PERCEPTION_VERSION,
            _OSM_SNAPSHOT_DATE,
            _IMAGERY_START,
            _IMAGERY_END,
            _PROPAGATION_VERSION,
            notes,
            city_id,
        ),
    )
    row = cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


def _insert_segment(cur: psycopg.Cursor[Any], offset_idx: int, city_id: Any) -> UUID:
    base_lon = _FIXTURE_LON - (offset_idx * 0.001)
    base_lat = _FIXTURE_LAT + (offset_idx * 0.001)
    geom = LineString([(base_lon, base_lat), (base_lon + 0.001, base_lat + 0.001)])
    cur.execute(
        """
        INSERT INTO road_segments (osm_way_id, geometry, attrs, city_id)
        VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), %s::jsonb, %s)
        RETURNING id
        """,
        (
            920_000 + offset_idx,
            wkb.dumps(geom),
            '{"highway": "primary"}',
            city_id,
        ),
    )
    row = cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


def _insert_score(
    cur: psycopg.Cursor[Any],
    segment_id: UUID,
    run_id: UUID,
    run_timestamp: datetime,
    city_id: Any,
    *,
    composite_risk: float,
    propagation_uplift: float,
    sub_lane: float,
    sub_glare: float,
    sub_junction: float,
    sub_historical: float,
    confidence: float = 0.8,
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
            composite_risk,
            sub_lane,
            sub_glare,
            sub_junction,
            sub_historical,
            confidence,
            run_id,
            run_timestamp,
            _PERCEPTION_VERSION,
            _OSM_SNAPSHOT_DATE,
            _IMAGERY_START,
            _IMAGERY_END,
            _PROPAGATION_VERSION,
            propagation_uplift,
            city_id,
        ),
    )


@pytest.fixture
def seed_delta_runs(
    owner_conn: psycopg.Connection[Any],
    cambridge_city_id: Any,
) -> tuple[UUID, UUID, UUID]:
    """Two scoring_runs at noon a week apart sharing one segment.

    Returns ``(run_a_id, run_b_id, shared_segment_id)``. Also seeds:

    * One segment present in run_a only — must NOT appear in delta rows.
    * One segment present at a different hour in run_a — must NOT
      appear when ``t`` resolves to noon.
    """
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, segment_imagery, road_segments CASCADE")
        run_a = _insert_scoring_run(
            cur, _RUN_A_TS, notes="task 2.6 tile delta — A", city_id=cambridge_city_id
        )
        run_b = _insert_scoring_run(
            cur, _RUN_B_TS, notes="task 2.6 tile delta — B", city_id=cambridge_city_id
        )
        # Shared segment at noon both runs.
        shared = _insert_segment(cur, 0, cambridge_city_id)
        _insert_score(
            cur,
            shared,
            run_a,
            _RUN_A_TS,
            cambridge_city_id,
            composite_risk=0.30,
            propagation_uplift=0.05,
            sub_lane=0.20,
            sub_glare=0.10,
            sub_junction=0.30,
            sub_historical=0.15,
            confidence=0.70,
        )
        _insert_score(
            cur,
            shared,
            run_b,
            _RUN_B_TS,
            cambridge_city_id,
            composite_risk=0.40,  # +0.10
            propagation_uplift=0.08,  # +0.03
            sub_lane=0.25,  # +0.05
            sub_glare=0.08,  # -0.02
            sub_junction=0.30,  # 0
            sub_historical=0.15,  # 0
            confidence=0.90,
        )
        # Run-a-only segment (must drop).
        only_a = _insert_segment(cur, 1, cambridge_city_id)
        _insert_score(
            cur,
            only_a,
            run_a,
            _RUN_A_TS,
            cambridge_city_id,
            composite_risk=0.5,
            propagation_uplift=0.0,
            sub_lane=0.5,
            sub_glare=0.0,
            sub_junction=0.0,
            sub_historical=0.0,
        )
        # Off-hour segment in both runs (1 PM, not noon).
        off_hour = _insert_segment(cur, 2, cambridge_city_id)
        _insert_score(
            cur,
            off_hour,
            run_a,
            _RUN_A_TS + timedelta(hours=1),
            cambridge_city_id,
            composite_risk=0.2,
            propagation_uplift=0.0,
            sub_lane=0.1,
            sub_glare=0.1,
            sub_junction=0.0,
            sub_historical=0.0,
        )
        _insert_score(
            cur,
            off_hour,
            run_b,
            _RUN_B_TS + timedelta(hours=1),
            cambridge_city_id,
            composite_risk=0.3,
            propagation_uplift=0.0,
            sub_lane=0.1,
            sub_glare=0.1,
            sub_junction=0.0,
            sub_historical=0.0,
        )
    owner_conn.commit()
    return run_a, run_b, shared


# ---------------------------------------------------------------------------
# Function existence
# ---------------------------------------------------------------------------


class TestTileDeltaFunctionShape:
    def test_bytes_function_exists(self, owner_conn: psycopg.Connection[Any]) -> None:
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public' AND p.proname = 'road_segments_tile_delta'
                """
            )
            assert cur.fetchone() is not None, f"function {TILE_FN_BYTES} not found"

    def test_rows_function_exists(self, owner_conn: psycopg.Connection[Any]) -> None:
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                  AND p.proname = 'road_segments_tile_delta_rows'
                """
            )
            assert cur.fetchone() is not None, f"function {TILE_FN_ROWS} not found"


# ---------------------------------------------------------------------------
# Row shape + delta math
# ---------------------------------------------------------------------------


class TestTileDeltaRowsCarryDecomposedDeltas:
    def test_rows_include_expected_columns(
        self,
        seed_delta_runs: tuple[UUID, UUID, UUID],
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        """The rows function projects the delta decomposition (composite,
        local, uplift) plus all four sub-score deltas plus both
        confidences — preserving the explainability invariant."""
        run_a, run_b, _shared = seed_delta_runs
        x, y = _lonlat_to_tile(_FIXTURE_LON, _FIXTURE_LAT, 14)
        with owner_conn.cursor() as cur:
            # Phase 4b (migration 0019): positional order is
            # (z, x, y, run_a, run_b, city_slug, t).
            cur.execute(
                f"SELECT * FROM {TILE_FN_ROWS}(%s, %s, %s, %s, %s, %s, %s::timestamptz)",
                (14, x, y, run_a, run_b, "cambridge", _RUN_B_TS.isoformat()),
            )
            colnames = [d.name for d in cur.description] if cur.description else []

        for col in (
            "id",
            "geom",
            "osm_way_id",
            "highway",
            "composite_delta",
            "local_contribution_delta",
            "propagation_uplift_delta",
            "sub_score_lane_marking_delta",
            "sub_score_glare_delta",
            "sub_score_junction_complexity_delta",
            "sub_score_historical_delta",
            "confidence_a",
            "confidence_b",
        ):
            assert col in colnames, (
                f"`{col}` column missing from {TILE_FN_ROWS} output; got {colnames}"
            )

    def test_delta_math_matches_b_minus_a(
        self,
        seed_delta_runs: tuple[UUID, UUID, UUID],
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        """For the shared segment, composite_delta = 0.40 - 0.30 = 0.10;
        propagation_uplift_delta = 0.08 - 0.05 = 0.03;
        local_contribution_delta = (0.40-0.08) - (0.30-0.05) = 0.07
        (i.e., composite_delta - propagation_uplift_delta)."""
        run_a, run_b, shared = seed_delta_runs
        x, y = _lonlat_to_tile(_FIXTURE_LON, _FIXTURE_LAT, 14)
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {TILE_FN_ROWS}(%s, %s, %s, %s, %s, %s, %s::timestamptz)",
                (14, x, y, run_a, run_b, "cambridge", _RUN_B_TS.isoformat()),
            )
            colnames = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchall()

        by_id = {r[colnames.index("id")]: r for r in rows}
        assert shared in by_id, "shared segment must appear in delta tile rows"
        row = by_id[shared]

        def col(name: str) -> Any:
            return row[colnames.index(name)]

        assert col("composite_delta") == pytest.approx(0.10, abs=1e-6)
        assert col("propagation_uplift_delta") == pytest.approx(0.03, abs=1e-6)
        assert col("local_contribution_delta") == pytest.approx(0.07, abs=1e-6)
        assert col("sub_score_lane_marking_delta") == pytest.approx(0.05, abs=1e-6)
        assert col("sub_score_glare_delta") == pytest.approx(-0.02, abs=1e-6)
        assert col("sub_score_junction_complexity_delta") == pytest.approx(0.0, abs=1e-6)
        assert col("sub_score_historical_delta") == pytest.approx(0.0, abs=1e-6)
        assert col("confidence_a") == pytest.approx(0.70, abs=1e-6)
        assert col("confidence_b") == pytest.approx(0.90, abs=1e-6)

    def test_rows_exclude_segments_not_in_both_runs(
        self,
        seed_delta_runs: tuple[UUID, UUID, UUID],
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        """Segment present only in run_a must NOT appear (INNER JOIN)."""
        run_a, run_b, shared = seed_delta_runs
        x, y = _lonlat_to_tile(_FIXTURE_LON, _FIXTURE_LAT, 14)
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {TILE_FN_ROWS}(%s, %s, %s, %s, %s, %s, %s::timestamptz)",
                (14, x, y, run_a, run_b, "cambridge", _RUN_B_TS.isoformat()),
            )
            ids = {r[0] for r in cur.fetchall()}
        assert shared in ids
        # The only-A and off-hour segments must not appear.
        assert len(ids) == 1, f"expected only the shared segment; got {ids}"

    def test_rows_exclude_off_hour_pairs(
        self,
        seed_delta_runs: tuple[UUID, UUID, UUID],
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        """The off-hour segment (present at 1 PM in both runs) must not
        appear when ``t`` resolves to noon."""
        run_a, run_b, _shared = seed_delta_runs
        x, y = _lonlat_to_tile(_FIXTURE_LON, _FIXTURE_LAT, 14)
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {TILE_FN_ROWS}(%s, %s, %s, %s, %s, %s, %s::timestamptz)",
                (14, x, y, run_a, run_b, "cambridge", _RUN_B_TS.isoformat()),
            )
            n = cur.fetchone()
        assert n is not None
        assert int(n[0]) == 1, "off-hour and only-A segments must not appear at noon"

    def test_null_t_defaults_to_noon(
        self,
        seed_delta_runs: tuple[UUID, UUID, UUID],
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        """Passing NULL for ``t`` must default to noon UTC so callers
        without a scrubber state still get a usable delta layer."""
        run_a, run_b, shared = seed_delta_runs
        x, y = _lonlat_to_tile(_FIXTURE_LON, _FIXTURE_LAT, 14)
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {TILE_FN_ROWS}(%s, %s, %s, %s, %s, %s, NULL::timestamptz)",
                (14, x, y, run_a, run_b, "cambridge"),
            )
            ids = {r[0] for r in cur.fetchall()}
        assert ids == {shared}


# ---------------------------------------------------------------------------
# Bytes wrapper
# ---------------------------------------------------------------------------


class TestTileDeltaBytesWrapper:
    def test_bytes_returns_non_empty_mvt(
        self,
        seed_delta_runs: tuple[UUID, UUID, UUID],
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        run_a, run_b, _shared = seed_delta_runs
        x, y = _lonlat_to_tile(_FIXTURE_LON, _FIXTURE_LAT, 14)
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT {TILE_FN_BYTES}(%s, %s, %s, %s, %s, %s, %s::timestamptz)",
                (14, x, y, run_a, run_b, "cambridge", _RUN_B_TS.isoformat()),
            )
            row = cur.fetchone()
        assert row is not None
        mvt = row[0]
        assert isinstance(mvt, (bytes, bytearray, memoryview))
        assert len(mvt) > 0

    def test_bytes_returns_empty_mvt_for_unknown_runs(
        self,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        """Unknown run UUIDs produce a well-formed empty MVT (no rows)
        rather than an error — the frontend can request the layer
        before run-picker UI settles without seeing a 500."""
        from uuid import uuid4

        x, y = _lonlat_to_tile(_FIXTURE_LON, _FIXTURE_LAT, 14)
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT {TILE_FN_BYTES}(%s, %s, %s, %s, %s, %s, NULL::timestamptz)",
                (14, x, y, uuid4(), uuid4(), "cambridge"),
            )
            row = cur.fetchone()
        assert row is not None
        mvt = row[0]
        # MVT for zero features serializes to either b'' or a tiny
        # empty layer envelope — either is acceptable.
        assert mvt is None or len(mvt) <= 64


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------


class TestTileDeltaGrants:
    """The app role must be able to EXECUTE both functions so the
    pg_tileserv service account can call them."""

    def test_app_role_can_execute_bytes_function(
        self,
        seed_delta_runs: tuple[UUID, UUID, UUID],
        app_conn: psycopg.Connection[Any],
    ) -> None:
        run_a, run_b, _shared = seed_delta_runs
        x, y = _lonlat_to_tile(_FIXTURE_LON, _FIXTURE_LAT, 14)
        with app_conn.cursor() as cur:
            cur.execute(
                f"SELECT {TILE_FN_BYTES}(%s, %s, %s, %s, %s, %s, %s::timestamptz)",
                (14, x, y, run_a, run_b, "cambridge", _RUN_B_TS.isoformat()),
            )
            row = cur.fetchone()
        assert row is not None

    def test_app_role_can_execute_rows_function(
        self,
        seed_delta_runs: tuple[UUID, UUID, UUID],
        app_conn: psycopg.Connection[Any],
    ) -> None:
        run_a, run_b, _shared = seed_delta_runs
        x, y = _lonlat_to_tile(_FIXTURE_LON, _FIXTURE_LAT, 14)
        with app_conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {TILE_FN_ROWS}(%s, %s, %s, %s, %s, %s, %s::timestamptz)",
                (14, x, y, run_a, run_b, "cambridge", _RUN_B_TS.isoformat()),
            )
            row = cur.fetchone()
        assert row is not None
        assert int(row[0]) == 1


# ---------------------------------------------------------------------------
# pg_tileserv HTTP smoke (skipped if container unreachable)
# ---------------------------------------------------------------------------


class TestPgTileservDeltaHttpEndpoint:
    BASE_URL = os.environ.get("TILE_BASE_URL", "http://localhost:7800")

    def _is_reachable(self) -> bool:
        try:
            r = httpx.get(f"{self.BASE_URL}/tiles/index.json", timeout=1.0)
            return r.status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    def test_endpoint_returns_200_for_delta_tile(
        self, seed_delta_runs: tuple[UUID, UUID, UUID]
    ) -> None:
        if not self._is_reachable():
            pytest.skip("pg_tileserv not reachable at " + self.BASE_URL)
        run_a, run_b, _shared = seed_delta_runs
        x, y = _lonlat_to_tile(_FIXTURE_LON, _FIXTURE_LAT, 14)
        url = f"{self.BASE_URL}/tiles/{TILE_FN_BYTES}/{14}/{x}/{y}.pbf"
        resp = httpx.get(
            url,
            params={
                "run_a": str(run_a),
                "run_b": str(run_b),
                # Phase 4b: city_slug is required (migration 0019).
                "city_slug": "cambridge",
                "t": _RUN_B_TS.isoformat(),
            },
            timeout=5.0,
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.content) > 0
