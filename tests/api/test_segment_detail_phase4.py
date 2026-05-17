"""Phase 4 segment-detail tests — Task 4.7.5.

Asserts ``GET /segments/{id}`` returns the new Phase 4 fields:

  - ``composite_risk`` (already present; now sourced from the Phase 4
    propagator).
  - ``local_contribution`` and ``propagation_uplift`` — the
    explainable decomposition.
  - ``propagation_algorithm`` — a typed ``{name, version}`` object,
    or ``None`` for pre-Phase-4 rows (sentinel branch).

Integration test — requires a running, migrated Postgres.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString

from api.main import create_app
from ingestion.osm import RoadSegment, SnapshotMetadata
from ingestion.persist import persist_road_segments

pytestmark = pytest.mark.integration


SEGMENTS = [
    RoadSegment(
        osm_way_id=99_201,
        geometry=LineString([(-71.110, 42.370), (-71.100, 42.370)]),
        attrs={"highway": "primary", "lanes": "2"},
    ),
]
METADATA = SnapshotMetadata(
    osm_snapshot_date=date(2026, 5, 13),
    source_url="file:///fixtures/phase-4-detail.osm",
    local_path=Path("/fixtures/phase-4-detail.osm"),
    size_bytes=1024,
    sha256="cafebabe",
)


@pytest.fixture(autouse=True)
def _clean(owner_conn: psycopg.Connection[Any]) -> None:
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, segment_imagery, road_segments CASCADE")
        cur.execute("DELETE FROM data_sources WHERE name = 'osm'")
    owner_conn.commit()


@pytest.fixture
def segment_id(
    database_url: str, owner_conn: psycopg.Connection[Any], cambridge_city_id: Any
) -> str:
    persist_road_segments(
        database_url, SEGMENTS, METADATA, source_name="osm", city_id=cambridge_city_id
    )
    with owner_conn.cursor() as cur:
        cur.execute("SELECT id FROM road_segments WHERE osm_way_id = 99201")
        row = cur.fetchone()
        assert row is not None
        return str(row[0])


def _seed_phase4_row(
    owner_conn: psycopg.Connection[Any],
    segment_id: str,
    *,
    composite: float,
    uplift: float,
    algorithm: str = "pagerank-diffusion-0.1.0",
) -> None:
    """Insert one scoring_runs + segment_scores row using the Phase 4 columns."""
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO scoring_runs (
                scoring_run_timestamp, perception_model_version, osm_snapshot_date,
                imagery_capture_window, propagation_algorithm_version
            ) VALUES (
                now(), 'lane-marking-standin-deadbeef', '2026-05-13',
                '[2025-06-01,2025-09-01)'::daterange, %s
            ) RETURNING id, scoring_run_timestamp
            """,
            (algorithm,),
        )
        row = cur.fetchone()
        assert row is not None
        run_id, run_ts = row
        cur.execute(
            """
            INSERT INTO segment_scores (
                segment_id, composite_risk, propagation_uplift,
                sub_score_lane_marking, sub_score_glare,
                sub_score_junction_complexity, sub_score_historical,
                confidence,
                is_stub_lane_marking, is_stub_glare,
                is_stub_junction_complexity, is_stub_historical,
                scoring_run_id, scoring_run_timestamp,
                perception_model_version, osm_snapshot_date,
                imagery_capture_window, propagation_algorithm_version
            ) VALUES (
                %s, %s, %s,
                0.55, 0.30, 0.50, 0.20,
                0.8,
                false, false, false, false,
                %s, %s,
                'lane-marking-standin-deadbeef', '2026-05-13',
                '[2025-06-01,2025-09-01)'::daterange, %s
            )
            """,
            (segment_id, composite, uplift, run_id, run_ts, algorithm),
        )
    owner_conn.commit()


def test_segment_detail_carries_phase4_fields(
    segment_id: str,
    owner_conn: psycopg.Connection[Any],
) -> None:
    _seed_phase4_row(owner_conn, segment_id, composite=0.65, uplift=0.15)

    with TestClient(create_app()) as client:
        response = client.get(f"/segments/{segment_id}")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["composite_risk"] == pytest.approx(0.65, abs=1e-6)
    assert body["propagation_uplift"] == pytest.approx(0.15, abs=1e-6)
    assert body["local_contribution"] == pytest.approx(0.50, abs=1e-6)
    # composite_risk = local_contribution + propagation_uplift
    assert body["local_contribution"] + body["propagation_uplift"] == pytest.approx(
        body["composite_risk"], abs=1e-6
    )
    algo = body["propagation_algorithm"]
    assert algo == {"name": "pagerank-diffusion", "version": "0.1.0"}


def test_segment_detail_sentinel_propagation_algorithm_is_none(
    segment_id: str,
    owner_conn: psycopg.Connection[Any],
) -> None:
    """Pre-Phase-4 rows persist ``propagation_algorithm_version='none-phase-2'``;
    the API surfaces ``propagation_algorithm: None`` rather than a fake label."""
    _seed_phase4_row(
        owner_conn,
        segment_id,
        composite=0.42,
        uplift=0.0,
        algorithm="none-phase-2",
    )

    with TestClient(create_app()) as client:
        response = client.get(f"/segments/{segment_id}")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["propagation_algorithm"] is None
    assert body["propagation_uplift"] == pytest.approx(0.0, abs=1e-6)


def test_segment_detail_falls_back_when_no_score(
    segment_id: str,
) -> None:
    """A segment with no scoring_run rows still returns a 200 with stub values
    + ``propagation_algorithm=None``."""
    with TestClient(create_app()) as client:
        response = client.get(f"/segments/{segment_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["propagation_algorithm"] is None
    # The stub branch sets propagation_uplift to 0 + local = composite.
    assert body["propagation_uplift"] == pytest.approx(0.0, abs=1e-6)
    assert body["local_contribution"] == pytest.approx(body["composite_risk"], abs=1e-6)


def test_t_query_param_resolves_phase4_row(
    segment_id: str,
    owner_conn: psycopg.Connection[Any],
) -> None:
    _seed_phase4_row(owner_conn, segment_id, composite=0.65, uplift=0.20)
    t = datetime.now(UTC).isoformat()
    with TestClient(create_app()) as client:
        response = client.get(f"/segments/{segment_id}", params={"t": t})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["propagation_algorithm"] == {
        "name": "pagerank-diffusion",
        "version": "0.1.0",
    }
    assert body["propagation_uplift"] == pytest.approx(0.20, abs=1e-6)
