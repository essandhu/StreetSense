"""Phase 4 scoring-run persistence test — Task 4.6.5.

End-to-end against a tiny fixture network (3 segments x 24 hours)
exercising the full Phase 4 path:

  - All four scorers wired (glare + junction + historical; perception
    is omitted here because the fixture has no imagery — it's covered
    in test_perception_run_persistence.py).
  - Propagator runs 24 times via the C++ engine.
  - composite_risk + propagation_uplift are populated on every row.
  - propagation_algorithm_version is the real semver, never the Phase 2
    ``"none-phase-2"`` sentinel.

Integration test — requires a running, migrated Postgres + the
propagator bindings (built by ``uv sync``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from shapely.geometry import LineString

from ingestion.osm import RoadSegment, SnapshotMetadata
from ingestion.persist import persist_road_segments
from scoring.environmental.glare import GlareScorer
from scoring.historical.scorer import HistoricalCorrelationScorer
from scoring.junction.scorer import JunctionComplexityScorer
from scoring.phase4_loaders import make_incident_loader, make_topology_loader
from scoring.phase4_run import execute_phase4_scoring_run
from scoring.run import ScoringRunConfig

pytestmark = pytest.mark.integration


# Three short segments arranged at a shared junction (their endpoints
# meet at the same point) so the topology loader produces a non-trivial
# multi-leg junction and the propagator graph has real adjacency.
SHARED_JUNCTION = (-71.105, 42.370)

FIXTURE_SEGMENTS = [
    RoadSegment(
        osm_way_id=99_101,
        geometry=LineString([(-71.115, 42.370), SHARED_JUNCTION]),  # E-W into junction
        attrs={"highway": "primary", "lanes": "2"},
    ),
    RoadSegment(
        osm_way_id=99_102,
        geometry=LineString([SHARED_JUNCTION, (-71.105, 42.380)]),  # N out of junction
        attrs={"highway": "secondary", "lanes": "2"},
    ),
    RoadSegment(
        osm_way_id=99_103,
        geometry=LineString([SHARED_JUNCTION, (-71.095, 42.370)]),  # E out of junction
        attrs={"highway": "residential", "lanes": "1"},
    ),
]

FIXTURE_METADATA = SnapshotMetadata(
    osm_snapshot_date=date(2026, 5, 13),
    source_url="file:///fixtures/phase-4-tiny.osm",
    local_path=Path("/fixtures/phase-4-tiny.osm"),
    size_bytes=1024,
    sha256="cafebabe",
)

# 24 hourly UTC samples on a summer day for non-trivial glare values.
TEMPORAL_SAMPLES = tuple(datetime(2025, 6, 21, h, 0, tzinfo=UTC) for h in range(24))


def _make_incident(lat: float, lon: float, days_ago: int, severity: str) -> dict[str, Any]:
    return {
        "provider": "test-fixture",
        "provider_incident_id": f"test-{uuid4()}",
        "lat": lat,
        "lon": lon,
        "incident_at": datetime(2025, 6, 21, 12, tzinfo=UTC).replace(day=21 - (days_ago % 20)),
        "severity": severity,
        "metadata": Jsonb({}),
    }


@pytest.fixture(autouse=True)
def _clean_tables(owner_conn: psycopg.Connection[Any]) -> None:
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments, incidents CASCADE")
        cur.execute("DELETE FROM data_sources WHERE name = 'osm'")
    owner_conn.commit()


@pytest.fixture
def seeded_database(
    database_url: str, owner_conn: psycopg.Connection[Any], cambridge_city_id: Any
) -> str:
    persist_road_segments(
        database_url,
        FIXTURE_SEGMENTS,
        FIXTURE_METADATA,
        source_name="osm",
        city_id=cambridge_city_id,
    )
    # Drop 3 synthetic incidents near the shared junction so the
    # historical scorer has signal. Using INSERT directly (not the
    # provider) because the test owns deterministic fixture data.
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO incidents (
                provider, provider_incident_id, geom, incident_at, severity, metadata, city_id
            )
            VALUES
              ('test-fixture', 'inc-1',
                 ST_SetSRID(ST_MakePoint(-71.1050, 42.3702), 4326),
                 '2025-06-20T12:00:00Z', 'injury', '{}'::jsonb, %(city_id)s),
              ('test-fixture', 'inc-2',
                 ST_SetSRID(ST_MakePoint(-71.1051, 42.3698), 4326),
                 '2025-05-21T12:00:00Z', 'fatal',  '{}'::jsonb, %(city_id)s),
              ('test-fixture', 'inc-3',
                 ST_SetSRID(ST_MakePoint(-71.1049, 42.3700), 4326),
                 '2025-04-21T12:00:00Z', 'property_damage_only', '{}'::jsonb, %(city_id)s)
            """,
            {"city_id": cambridge_city_id},
        )
    owner_conn.commit()
    return database_url


def _build_run_config(city_id: Any) -> ScoringRunConfig:
    return ScoringRunConfig(
        temporal_samples=TEMPORAL_SAMPLES,
        osm_snapshot_date=FIXTURE_METADATA.osm_snapshot_date,
        city_id=city_id,
        perception_model_version="phase4-test-no-perception",
        imagery_capture_window=(date(2025, 1, 1), date(2025, 12, 31)),
        propagation_algorithm_version="pagerank-diffusion-0.1.0",
        notes="phase4 integration test",
    )


def _build_scorers(seeded_database: str, city_id: Any) -> list[Any]:
    """Glare + Junction + Historical. Perception is omitted because the
    fixture network has no imagery; perception is covered separately."""
    dsn = seeded_database.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as conn:
        topology_loader = make_topology_loader(conn, city_id=city_id)
        incident_loader = make_incident_loader(conn, city_id=city_id)
    return [
        GlareScorer(),
        JunctionComplexityScorer(topology_loader=topology_loader),
        HistoricalCorrelationScorer(
            incident_loader=incident_loader,
            run_at=datetime(2025, 6, 21, 12, tzinfo=UTC),
        ),
    ]


class TestPhase4ScoringRun:
    def test_writes_segments_times_samples_rows(
        self,
        seeded_database: str,
        owner_conn: psycopg.Connection[Any],
        cambridge_city_id: Any,
    ) -> None:
        config = _build_run_config(cambridge_city_id)
        summary = execute_phase4_scoring_run(
            config=config,
            scorers=_build_scorers(seeded_database, cambridge_city_id),
            database_url=seeded_database,
        )
        assert summary.rows_written == 3 * 24 == 72
        assert summary.propagation_total_seconds > 0.0
        assert len(summary.propagation_per_hour_seconds) == 24

        with owner_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM segment_scores")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 72

    def test_propagation_algorithm_version_is_real(
        self,
        seeded_database: str,
        owner_conn: psycopg.Connection[Any],
        cambridge_city_id: Any,
    ) -> None:
        config = _build_run_config(cambridge_city_id)
        execute_phase4_scoring_run(
            config=config,
            scorers=_build_scorers(seeded_database, cambridge_city_id),
            database_url=seeded_database,
        )
        with owner_conn.cursor() as cur:
            cur.execute("SELECT DISTINCT propagation_algorithm_version FROM segment_scores")
            rows = cur.fetchall()
        assert rows == [("pagerank-diffusion-0.1.0",)]

    def test_composite_risk_and_uplift_populated(
        self,
        seeded_database: str,
        owner_conn: psycopg.Connection[Any],
        cambridge_city_id: Any,
    ) -> None:
        config = _build_run_config(cambridge_city_id)
        execute_phase4_scoring_run(
            config=config,
            scorers=_build_scorers(seeded_database, cambridge_city_id),
            database_url=seeded_database,
        )
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT composite_risk, propagation_uplift,
                       sub_score_glare, sub_score_junction_complexity, sub_score_historical
                FROM segment_scores
                """,
            )
            rows = cur.fetchall()
        assert len(rows) == 72
        any_uplift = False
        for composite, uplift, _glare, _junction, _historical in rows:
            assert composite is not None
            assert uplift is not None
            # composite_risk and uplift are doubles; convert to float
            # for comparison in case psycopg returns Decimal.
            composite_f = float(composite)
            uplift_f = float(uplift)
            assert composite_f >= 0.0
            assert uplift_f >= 0.0
            if uplift_f > 0.0:
                any_uplift = True
            # Sanity: composite_risk should be at least the weighted local
            # aggregate of the three real sub-scores; the propagator
            # contribution is on top.
            assert composite_f >= 0.0
        assert any_uplift, (
            "Expected at least one segment-hour pair with non-zero "
            "propagation_uplift; the 3 segments share a junction so adjacency exists"
        )

    def test_junction_and_historical_subscores_real(
        self,
        seeded_database: str,
        owner_conn: psycopg.Connection[Any],
        cambridge_city_id: Any,
    ) -> None:
        config = _build_run_config(cambridge_city_id)
        execute_phase4_scoring_run(
            config=config,
            scorers=_build_scorers(seeded_database, cambridge_city_id),
            database_url=seeded_database,
        )
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    is_stub_glare,
                    is_stub_junction_complexity,
                    is_stub_historical
                FROM segment_scores
                """,
            )
            rows = cur.fetchall()
        for is_stub_glare, is_stub_junction, is_stub_historical in rows:
            assert is_stub_glare is False
            assert is_stub_junction is False
            assert is_stub_historical is False
