"""Tile rows carry ``lane_marking_quality`` (Phase 3.5.6).

Phase 3 extends `road_segments_tile_t_rows` with a
``lane_marking_quality`` column (migration 0010). This test asserts:

- The new column is present in the row function's RETURNS TABLE.
- A scoring run with both glare + perception writes real values that
  propagate to the tile row.
- Existing Phase 2 columns (`glare_score`, four `is_stub_*` flags)
  remain — additive change.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import onnxruntime as ort
import psycopg
import pytest
from shapely import wkb
from shapely.geometry import LineString

from scoring.environmental.glare import GlareScorer
from scoring.perception.scorer import ImageryLoader, PerceptionScorer
from scoring.run import ScoringRun, ScoringRunConfig

pytestmark = pytest.mark.integration

TILE_FN_ROWS = "public.road_segments_tile_t_rows"

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "perception"
_STANDIN_PATH = _FIXTURE_ROOT / "standin.onnx"
_IMAGES_DIR = _FIXTURE_ROOT / "images"


def _lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


@pytest.fixture(autouse=True)
def _reset(owner_conn: psycopg.Connection[Any]) -> None:
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_imagery, segment_scores, scoring_runs, road_segments CASCADE")
    owner_conn.commit()


@pytest.fixture
def seeded_segment_with_scoring_run(
    owner_conn: psycopg.Connection[Any], database_url: str, cambridge_city_id: Any
) -> UUID:
    """Insert one Cambridge segment + run a scoring run with glare + perception."""
    geom = LineString([(-71.110, 42.370), (-71.100, 42.370)])
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO road_segments (osm_way_id, geometry, attrs, city_id)
            VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326),
                    '{"highway": "primary"}'::jsonb, %s)
            RETURNING id
            """,
            (901_001, wkb.dumps(geom), cambridge_city_id),
        )
        row = cur.fetchone()
        assert row is not None
        seg_id = row[0]
    owner_conn.commit()

    session = ort.InferenceSession(str(_STANDIN_PATH), providers=["CPUExecutionProvider"])
    fixture_bytes = [(p.stem, p.read_bytes()) for p in sorted(_IMAGES_DIR.glob("*.png"))]

    def loader(_segment_id: UUID) -> Iterable[tuple[str, bytes]]:
        return fixture_bytes

    perception_loader: ImageryLoader = loader

    config = ScoringRunConfig(
        temporal_samples=(datetime(2025, 6, 21, 16, 0, tzinfo=UTC),),
        osm_snapshot_date=date(2025, 6, 21),
        city_id=cambridge_city_id,
        perception_model_version="lane-marking-standin-deadbeef",
        imagery_capture_window=(date(2025, 6, 1), date(2025, 8, 31)),
    )
    ScoringRun(
        config=config,
        scorers=[
            GlareScorer(),
            PerceptionScorer(session=session, imagery_loader=perception_loader),
        ],
        database_url=database_url,
    ).execute()

    return seg_id


def test_rows_function_returns_lane_marking_quality(
    seeded_segment_with_scoring_run: UUID,
    owner_conn: psycopg.Connection[Any],
) -> None:
    del seeded_segment_with_scoring_run

    x, y = _lonlat_to_tile(-71.105, 42.370, 14)
    with owner_conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM {TILE_FN_ROWS}(%s, %s, %s, %s::timestamptz)",
            (14, x, y, "2025-06-21T16:00:00Z"),
        )
        colnames = [d.name for d in cur.description] if cur.description else []
        rows = cur.fetchall()

    assert "lane_marking_quality" in colnames, (
        f"`lane_marking_quality` missing from {TILE_FN_ROWS} output; got {colnames}"
    )
    # Phase 2 columns still present (regression).
    for c in (
        "glare_score",
        "is_stub_lane_marking",
        "is_stub_glare",
        "is_stub_junction_complexity",
        "is_stub_historical",
    ):
        assert c in colnames, f"Phase 2 column `{c}` missing"

    assert len(rows) >= 1, "fixture segment must be inside tile bbox"

    idx_lm = colnames.index("lane_marking_quality")
    idx_stub_lm = colnames.index("is_stub_lane_marking")
    idx_stub_glare = colnames.index("is_stub_glare")
    for row in rows:
        assert row[idx_stub_glare] is False
        assert row[idx_stub_lm] is False
        assert row[idx_lm] is not None
        assert 0.0 <= float(row[idx_lm]) <= 1.0


def test_bytes_function_still_returns_non_empty_mvt(
    seeded_segment_with_scoring_run: UUID,
    owner_conn: psycopg.Connection[Any],
) -> None:
    del seeded_segment_with_scoring_run
    x, y = _lonlat_to_tile(-71.105, 42.370, 14)
    with owner_conn.cursor() as cur:
        cur.execute(
            "SELECT public.road_segments_tile_t(%s, %s, %s, %s::timestamptz)",
            (14, x, y, "2025-06-21T16:00:00Z"),
        )
        row = cur.fetchone()
    assert row is not None
    mvt = row[0]
    assert isinstance(mvt, (bytes, bytearray, memoryview))
    assert len(mvt) > 0
