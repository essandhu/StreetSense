"""Integration tests for the Phase 3 scoring-run path (Task 3.4.3).

End-to-end:

- A tiny fixture network of 3 segments x 4 timestamps.
- Both GlareScorer (Phase 2) and PerceptionScorer (Phase 3) wired in.
- PerceptionScorer fed by an in-memory imagery loader returning the
  committed fixture-image bytes — no MinIO required.

Asserts:

- 12 `segment_scores` rows.
- `is_stub_glare = False`, `is_stub_lane_marking = False`,
  `is_stub_junction_complexity = True`, `is_stub_historical = True`.
- `perception_model_version` is the real value passed in (not the
  Phase 2 sentinel).
- `imagery_capture_window` is the real `(min, max)` daterange.
- `propagation_algorithm_version` retains the Phase 2 sentinel
  `"none-phase-2"` (regression guard).
"""

from __future__ import annotations

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

from scoring import PHASE_2_PROPAGATION_SENTINEL
from scoring.environmental.glare import GlareScorer
from scoring.perception.scorer import ImageryLoader, PerceptionScorer
from scoring.run import ScoringRun, ScoringRunConfig

pytestmark = pytest.mark.integration


_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "perception"
_STANDIN_PATH = _FIXTURE_ROOT / "standin.onnx"
_IMAGES_DIR = _FIXTURE_ROOT / "images"


@pytest.fixture(scope="module")
def session() -> ort.InferenceSession:
    return ort.InferenceSession(str(_STANDIN_PATH), providers=["CPUExecutionProvider"])


@pytest.fixture
def all_fixture_image_bytes() -> list[tuple[str, bytes]]:
    return [(p.stem, p.read_bytes()) for p in sorted(_IMAGES_DIR.glob("*.png"))]


@pytest.fixture(autouse=True)
def _reset_tables(owner_conn: psycopg.Connection[Any]) -> None:
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_imagery, segment_scores, scoring_runs, road_segments CASCADE")
    owner_conn.commit()


@pytest.fixture
def three_segments(owner_conn: psycopg.Connection[Any]) -> list[UUID]:
    """Three short east-west segments in central Cambridge."""
    geoms = [
        LineString([(-71.106, 42.371), (-71.104, 42.371)]),
        LineString([(-71.107, 42.372), (-71.103, 42.372)]),
        LineString([(-71.108, 42.373), (-71.102, 42.373)]),
    ]
    ids: list[UUID] = []
    with owner_conn.cursor() as cur:
        for i, geom in enumerate(geoms):
            cur.execute(
                """
                INSERT INTO road_segments (osm_way_id, geometry, attrs)
                VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), '{}'::jsonb)
                RETURNING id
                """,
                (888_000 + i, wkb.dumps(geom)),
            )
            row = cur.fetchone()
            assert row is not None
            ids.append(row[0])
    owner_conn.commit()
    return ids


def _loader_returning(items: list[tuple[str, bytes]]) -> ImageryLoader:
    def _load(_segment_id: UUID) -> Iterable[tuple[str, bytes]]:
        return items

    return _load


def _samples_4() -> tuple[datetime, ...]:
    base = datetime(2026, 6, 21, tzinfo=UTC)
    return tuple(base.replace(hour=h) for h in (5, 10, 17, 23))


def _config(
    *,
    perception_model_version: str,
    imagery_capture_window: tuple[date, date],
) -> ScoringRunConfig:
    return ScoringRunConfig(
        temporal_samples=_samples_4(),
        osm_snapshot_date=date(2026, 5, 14),
        perception_model_version=perception_model_version,
        imagery_capture_window=imagery_capture_window,
    )


def test_writes_12_rows_glare_and_lane_real(
    database_url: str,
    owner_conn: psycopg.Connection[Any],
    three_segments: list[UUID],
    session: ort.InferenceSession,
    all_fixture_image_bytes: list[tuple[str, bytes]],
) -> None:
    del three_segments  # consumed via _reset_tables / population fixture
    perception_model_version = "lane-marking-standin-8a1627c46d58"
    imagery_window = (date(2025, 6, 1), date(2025, 8, 31))

    config = _config(
        perception_model_version=perception_model_version,
        imagery_capture_window=imagery_window,
    )
    perception = PerceptionScorer(
        session=session,
        imagery_loader=_loader_returning(all_fixture_image_bytes),
    )

    summary = ScoringRun(
        config=config,
        scorers=[GlareScorer(), perception],
        database_url=database_url,
    ).execute()

    assert summary.rows_written == 12  # 3 segments x 4 timestamps

    with owner_conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                is_stub_glare,
                is_stub_lane_marking,
                is_stub_junction_complexity,
                is_stub_historical,
                perception_model_version,
                lower(imagery_capture_window)::text,
                upper(imagery_capture_window)::text,
                propagation_algorithm_version
            FROM segment_scores
            """
        )
        rows = cur.fetchall()

    assert len(rows) == 12
    for row in rows:
        (
            stub_glare,
            stub_lane,
            stub_junction,
            stub_historical,
            model_v,
            window_lower,
            window_upper,
            prop_v,
        ) = row
        assert stub_glare is False
        assert stub_lane is False
        assert stub_junction is True
        assert stub_historical is True
        assert model_v == perception_model_version
        assert window_lower == "2025-06-01"
        # Half-open: upper is end + 1 day.
        assert window_upper == "2025-09-01"
        assert prop_v == PHASE_2_PROPAGATION_SENTINEL


def test_stub_fallback_when_zero_imagery_for_segment(
    database_url: str,
    owner_conn: psycopg.Connection[Any],
    three_segments: list[UUID],
    session: ort.InferenceSession,
) -> None:
    """A segment with zero imagery yields a stub lane_marking row."""
    del three_segments

    config = _config(
        perception_model_version="lane-marking-standin-8a1627c46d58",
        imagery_capture_window=(date(2025, 6, 1), date(2025, 8, 31)),
    )
    # Loader returns no imagery for any segment.
    perception = PerceptionScorer(
        session=session,
        imagery_loader=_loader_returning([]),
    )

    ScoringRun(
        config=config,
        scorers=[GlareScorer(), perception],
        database_url=database_url,
    ).execute()

    with owner_conn.cursor() as cur:
        cur.execute(
            """
            SELECT is_stub_glare, is_stub_lane_marking, sub_score_lane_marking
            FROM segment_scores
            """
        )
        rows = cur.fetchall()

    assert rows, "expected segment_scores rows"
    for is_stub_glare, is_stub_lane, lane_value in rows:
        # Glare is still real (it doesn't need imagery).
        assert is_stub_glare is False
        # Perception falls back to stub when no imagery.
        assert is_stub_lane is True
        # Stub-fallback value is exactly 0.0 (spec Tech Note 4 / scorer impl).
        assert lane_value == 0.0


def test_propagation_sentinel_is_retained(
    database_url: str,
    owner_conn: psycopg.Connection[Any],
    three_segments: list[UUID],
    session: ort.InferenceSession,
    all_fixture_image_bytes: list[tuple[str, bytes]],
) -> None:
    """Regression guard: a refactor must not accidentally drop the sentinel."""
    del three_segments

    config = _config(
        perception_model_version="lane-marking-standin-8a1627c46d58",
        imagery_capture_window=(date(2025, 6, 1), date(2025, 8, 31)),
    )
    perception = PerceptionScorer(
        session=session,
        imagery_loader=_loader_returning(all_fixture_image_bytes),
    )

    ScoringRun(
        config=config,
        scorers=[GlareScorer(), perception],
        database_url=database_url,
    ).execute()

    with owner_conn.cursor() as cur:
        cur.execute("SELECT DISTINCT propagation_algorithm_version FROM segment_scores")
        rows = cur.fetchall()
    assert rows == [(PHASE_2_PROPAGATION_SENTINEL,)]
