"""End-to-end persistence test for `ScoringRun.execute()` — Task 2.3.2.

Asserts that running the scoring orchestration against a tiny fixture
network (3 segments x 4 timestamps) writes exactly 12 ``segment_scores``
rows, each carrying:

- All six reproducibility fields populated.
- ``sub_score_glare`` is real (``is_stub_glare = false``).
- The other three sub-scores carry ``is_stub_* = true``.

Integration test — requires a running, migrated Postgres.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from shapely.geometry import LineString

from ingestion.osm import RoadSegment, SnapshotMetadata
from ingestion.persist import persist_road_segments
from scoring import PHASE_2_PROPAGATION_SENTINEL
from scoring.environmental.glare import GlareScorer
from scoring.run import (
    PHASE_2_IMAGERY_WINDOW_SENTINEL,
    PHASE_2_PERCEPTION_MODEL_VERSION_SENTINEL,
    ScoringRun,
    ScoringRunConfig,
)

pytestmark = pytest.mark.integration


# Three short segments in Cambridge, MA — distinct headings so the glare
# scorer produces a distinct value per segment.
FIXTURE_SEGMENTS = [
    RoadSegment(
        osm_way_id=99_001,
        geometry=LineString([(-71.110, 42.370), (-71.100, 42.370)]),  # east-west
        attrs={"highway": "primary"},
    ),
    RoadSegment(
        osm_way_id=99_002,
        geometry=LineString([(-71.105, 42.365), (-71.105, 42.375)]),  # north-south
        attrs={"highway": "secondary"},
    ),
    RoadSegment(
        osm_way_id=99_003,
        geometry=LineString([(-71.110, 42.365), (-71.100, 42.375)]),  # NE-SW
        attrs={"highway": "residential"},
    ),
]

FIXTURE_METADATA = SnapshotMetadata(
    osm_snapshot_date=date(2026, 5, 13),
    source_url="file:///fixtures/phase-2-tiny.osm",
    local_path=Path("/fixtures/phase-2-tiny.osm"),
    size_bytes=1024,
    sha256="cafebabe",
)

TEMPORAL_SAMPLES = (
    datetime(2025, 6, 21, 6, 0, tzinfo=UTC),  # ~02:00 EDT — sun below horizon
    datetime(2025, 6, 21, 10, 0, tzinfo=UTC),  # ~06:00 EDT — low east sun
    datetime(2025, 6, 21, 16, 50, tzinfo=UTC),  # ~solar noon Cambridge
    datetime(2025, 6, 21, 22, 0, tzinfo=UTC),  # ~18:00 EDT — low west sun
)


@pytest.fixture(autouse=True)
def _clean_tables(owner_conn: psycopg.Connection[Any]) -> None:
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments CASCADE")
        cur.execute("DELETE FROM data_sources WHERE name = 'osm'")
    owner_conn.commit()


@pytest.fixture
def seeded_database(database_url: str, cambridge_city_id: Any) -> str:
    """Insert the fixture segments and yield the DSN."""
    persist_road_segments(
        database_url,
        FIXTURE_SEGMENTS,
        FIXTURE_METADATA,
        source_name="osm",
        city_id=cambridge_city_id,
    )
    return database_url


def _run_with_glare(seeded_database: str) -> ScoringRunConfig:
    return ScoringRunConfig(
        temporal_samples=TEMPORAL_SAMPLES,
        osm_snapshot_date=FIXTURE_METADATA.osm_snapshot_date,
    )


class TestEndToEndScoringRun:
    def test_writes_segments_times_samples_rows(
        self,
        seeded_database: str,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        config = _run_with_glare(seeded_database)
        run = ScoringRun(
            config=config,
            scorers=[GlareScorer()],
            database_url=seeded_database,
        )
        summary = run.execute()

        assert summary.rows_written == len(FIXTURE_SEGMENTS) * len(TEMPORAL_SAMPLES) == 12
        assert summary.segments_processed == 3
        assert summary.temporal_samples_count == 4

        with owner_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM segment_scores")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 12

    def test_all_six_reproducibility_fields_populated(
        self,
        seeded_database: str,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        config = _run_with_glare(seeded_database)
        ScoringRun(
            config=config,
            scorers=[GlareScorer()],
            database_url=seeded_database,
        ).execute()

        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    scoring_run_id,
                    scoring_run_timestamp,
                    perception_model_version,
                    osm_snapshot_date,
                    imagery_capture_window,
                    propagation_algorithm_version
                FROM segment_scores
                """
            )
            rows = cur.fetchall()
        assert len(rows) == 12
        for row in rows:
            assert all(field is not None for field in row), f"Null reproducibility field in {row}"

    def test_glare_real_other_subscores_stubbed(
        self,
        seeded_database: str,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        config = _run_with_glare(seeded_database)
        ScoringRun(
            config=config,
            scorers=[GlareScorer()],
            database_url=seeded_database,
        ).execute()

        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    sub_score_glare,
                    is_stub_glare,
                    is_stub_lane_marking,
                    is_stub_junction_complexity,
                    is_stub_historical
                FROM segment_scores
                """
            )
            rows = cur.fetchall()

        for sub_glare, is_stub_glare, is_stub_lm, is_stub_jc, is_stub_hist in rows:
            assert sub_glare is not None
            assert is_stub_glare is False
            assert is_stub_lm is True
            assert is_stub_jc is True
            assert is_stub_hist is True

    def test_phase_2_sentinels_used_for_non_real_provenance(
        self,
        seeded_database: str,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        config = _run_with_glare(seeded_database)
        ScoringRun(
            config=config,
            scorers=[GlareScorer()],
            database_url=seeded_database,
        ).execute()

        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT
                    perception_model_version,
                    propagation_algorithm_version
                FROM segment_scores
                """
            )
            rows = cur.fetchall()
        assert rows == [(PHASE_2_PERCEPTION_MODEL_VERSION_SENTINEL, PHASE_2_PROPAGATION_SENTINEL)]

    def test_scoring_run_timestamp_matches_temporal_sample(
        self,
        seeded_database: str,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        """Each row's ``scoring_run_timestamp`` is the *temporal sample*
        that produced it — not the wallclock at run-time. The tile API
        snaps `t` to this column."""
        config = _run_with_glare(seeded_database)
        ScoringRun(
            config=config,
            scorers=[GlareScorer()],
            database_url=seeded_database,
        ).execute()

        with owner_conn.cursor() as cur:
            cur.execute("SELECT DISTINCT scoring_run_timestamp FROM segment_scores ORDER BY 1")
            rows = cur.fetchall()
        stored = [r[0] for r in rows]
        assert stored == sorted(TEMPORAL_SAMPLES)

    def test_sub_glare_changes_with_time_for_east_west_segment(
        self,
        seeded_database: str,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        """Sanity: the east-west segment shows distinct glare values at
        the four temporal samples (night vs morning vs noon vs evening)
        — proving the run actually invokes the scorer per-timestamp."""
        config = _run_with_glare(seeded_database)
        ScoringRun(
            config=config,
            scorers=[GlareScorer()],
            database_url=seeded_database,
        ).execute()

        with owner_conn.cursor() as cur:
            cur.execute("SELECT id FROM road_segments WHERE osm_way_id = 99001")
            seg_row = cur.fetchone()
            assert seg_row is not None
            seg_id = seg_row[0]

            cur.execute(
                """
                SELECT scoring_run_timestamp, sub_score_glare
                FROM segment_scores
                WHERE segment_id = %s
                ORDER BY scoring_run_timestamp
                """,
                (seg_id,),
            )
            rows = cur.fetchall()
        assert len(rows) == 4
        values = [r[1] for r in rows]
        assert len(set(values)) > 1, f"Glare should vary across time samples, got {values}"
        # Sun-below-horizon sample (first) must be exactly zero.
        assert values[0] == 0.0

    def test_imagery_capture_window_uses_sentinel(
        self,
        seeded_database: str,
        owner_conn: psycopg.Connection[Any],
    ) -> None:
        config = _run_with_glare(seeded_database)
        ScoringRun(
            config=config,
            scorers=[GlareScorer()],
            database_url=seeded_database,
        ).execute()

        # Verify the daterange round-trips to the documented sentinel form.
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT lower(imagery_capture_window)::text,
                                upper(imagery_capture_window)::text
                FROM segment_scores
                """
            )
            rows = cur.fetchall()
        assert rows == [("1970-01-01", "1970-01-02")]
        # Sentinel constant: Phase 3 changed the type from `str` to
        # `tuple[date, date]`; the persisted daterange string is now
        # derived. The tuple form represents a single inclusive day
        # (1970-01-01); persistence emits the half-open
        # `[1970-01-01, 1970-01-02)` byte-identical to the Phase 2
        # string sentinel.
        assert (date(1970, 1, 1), date(1970, 1, 1)) == PHASE_2_IMAGERY_WINDOW_SENTINEL
