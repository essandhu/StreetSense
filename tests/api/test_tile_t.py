"""Tile-with-`t` endpoint — Task 2.4.4.

Asserts the time-parameterized tile pipeline:

- The PostGIS function `public.road_segments_tile_t(z, x, y, t)` exists
  and returns ``bytea`` (MVT bytes).
- Calling the function returns non-empty bytes when a tile covers
  fixtured segments and a scoring run has executed.
- A companion table-returning function
  `public.road_segments_tile_t_rows(z, x, y, t)` exposes the per-feature
  row set the MVT is built from, so tests can assert that each feature
  carries ``glare_score`` and four ``is_stub_*`` boolean properties.
- The pg_tileserv HTTP endpoint serves the function via the ``t`` query
  parameter and returns 200 with a non-empty body. The pg_tileserv
  HTTP portion is skipped if the tile server is not reachable.

Integration test — requires a running, migrated Postgres. The HTTP
sub-tests additionally require the ``pg_tileserv`` container.
"""

from __future__ import annotations

import math
import os
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import httpx
import psycopg
import pytest
from shapely import wkb
from shapely.geometry import LineString

from scoring.environmental.glare import GlareScorer
from scoring.run import ScoringRun, ScoringRunConfig

pytestmark = pytest.mark.integration

TILE_FN_BYTES = "public.road_segments_tile_t"
TILE_FN_ROWS = "public.road_segments_tile_t_rows"

CAMBRIDGE_BBOX = (-71.16, 42.35, -71.07, 42.41)
TEMPORAL_SAMPLES = tuple(datetime(2025, 6, 21, h, 0, tzinfo=UTC) for h in range(24))


def _lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


@pytest.fixture
def seeded_run(
    owner_conn: psycopg.Connection[Any], database_url: str, cambridge_city_id: Any
) -> UUID:
    geom = LineString([(-71.110, 42.370), (-71.100, 42.370)])  # east-west, inside Cambridge
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments CASCADE")
        cur.execute(
            """
            INSERT INTO road_segments (osm_way_id, geometry, attrs, city_id)
            VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), %s::jsonb, %s)
            RETURNING id
            """,
            (888_001, wkb.dumps(geom), '{"highway": "primary"}', cambridge_city_id),
        )
        row = cur.fetchone()
        assert row is not None
        seg_id: UUID = row[0]
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


class TestTileFunctionShape:
    def test_bytes_function_exists(self, owner_conn: psycopg.Connection[Any]) -> None:
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public' AND p.proname = 'road_segments_tile_t'
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
                WHERE n.nspname = 'public' AND p.proname = 'road_segments_tile_t_rows'
                """
            )
            assert cur.fetchone() is not None, f"function {TILE_FN_ROWS} not found"


class TestTileRowsCarryGlareAndStubFlags:
    def test_rows_include_expected_columns(
        self, seeded_run: UUID, owner_conn: psycopg.Connection[Any]
    ) -> None:
        x, y = _lonlat_to_tile(-71.105, 42.370, 14)
        t = "2025-06-21T16:00:00Z"
        with owner_conn.cursor() as cur:
            # Phase 4b: city_slug is required; positional order is
            # (z, x, y, city_slug, t).
            cur.execute(
                f"SELECT * FROM {TILE_FN_ROWS}(%s, %s, %s, %s, %s::timestamptz)",
                (14, x, y, "cambridge", t),
            )
            colnames = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchall()

        assert any(c == "glare_score" for c in colnames), (
            f"`glare_score` column missing from {TILE_FN_ROWS} output; got {colnames}"
        )
        for flag in (
            "is_stub_lane_marking",
            "is_stub_glare",
            "is_stub_junction_complexity",
            "is_stub_historical",
        ):
            assert flag in colnames, f"`{flag}` column missing from {TILE_FN_ROWS} output"

        assert len(rows) >= 1, "tile covering Cambridge must include the fixture segment"

        # The fixture segment carries a real glare value at this time.
        idx_glare = colnames.index("glare_score")
        idx_is_stub_glare = colnames.index("is_stub_glare")
        idx_is_stub_lm = colnames.index("is_stub_lane_marking")
        for row in rows:
            assert row[idx_is_stub_glare] is False
            assert row[idx_is_stub_lm] is True
            assert row[idx_glare] is not None

    def test_bytes_function_returns_non_empty_mvt(
        self, seeded_run: UUID, owner_conn: psycopg.Connection[Any]
    ) -> None:
        x, y = _lonlat_to_tile(-71.105, 42.370, 14)
        with owner_conn.cursor() as cur:
            cur.execute(
                f"SELECT {TILE_FN_BYTES}(%s, %s, %s, %s, %s::timestamptz)",
                (14, x, y, "cambridge", "2025-06-21T16:00:00Z"),
            )
            row = cur.fetchone()
        assert row is not None
        mvt = row[0]
        assert isinstance(mvt, (bytes, bytearray, memoryview))
        assert len(mvt) > 0


class TestPgTileservHttpEndpoint:
    """HTTP-level smoke test against pg_tileserv. Skipped if the
    container is not reachable."""

    BASE_URL = os.environ.get("TILE_BASE_URL", "http://localhost:7800")

    def _is_reachable(self) -> bool:
        try:
            r = httpx.get(f"{self.BASE_URL}/tiles/index.json", timeout=1.0)
            return r.status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    def test_endpoint_with_t_returns_200_and_non_empty_body(self, seeded_run: UUID) -> None:
        if not self._is_reachable():
            pytest.skip("pg_tileserv not reachable at " + self.BASE_URL)
        x, y = _lonlat_to_tile(-71.105, 42.370, 14)
        url = f"{self.BASE_URL}/tiles/{TILE_FN_BYTES}/{14}/{x}/{y}.pbf"
        resp = httpx.get(
            url,
            # Phase 4b: city_slug is required (migration 0019).
            params={"t": "2025-06-21T16:00:00Z", "city_slug": "cambridge"},
            timeout=5.0,
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.content) > 0
