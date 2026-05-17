"""GET /segments/{id}?t=... — Task 2.4.1.

The endpoint accepts an ISO-8601 UTC ``t`` parameter and returns the
``segment_scores`` row snapped to the nearest persisted hourly sample.
The response shape replaces Phase 1's top-level ``risk_stub`` with
per-sub-score ``is_stub`` flags.
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

from scoring.environmental.glare import GlareScorer
from scoring.run import ScoringRun, ScoringRunConfig

pytestmark = pytest.mark.integration


# 24 hourly samples on the summer solstice — same default as `make scoring-run`.
TEMPORAL_SAMPLES = tuple(datetime(2025, 6, 21, h, 0, tzinfo=UTC) for h in range(24))


@pytest.fixture
def seeded_run(
    owner_conn: psycopg.Connection[Any], database_url: str, cambridge_city_id: Any
) -> UUID:
    """Seed one east-west segment, run scoring for 24 hourly samples, return seg id."""
    geom = LineString([(-71.110, 42.370), (-71.090, 42.370)])
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments CASCADE")
        cur.execute(
            """
            INSERT INTO road_segments (osm_way_id, geometry, attrs, city_id)
            VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), %s::jsonb, %s)
            RETURNING id
            """,
            (777_001, wkb.dumps(geom), '{"highway": "primary"}', cambridge_city_id),
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


class TestSegmentDetailWithT:
    @pytest.mark.asyncio
    async def test_response_includes_four_subscores_with_is_stub_flags(
        self, api_client: AsyncClient, seeded_run: UUID
    ) -> None:
        resp = await api_client.get(
            f"/api/cities/cambridge/segments/{seeded_run}",
            params={"t": "2025-06-21T12:00:00Z"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Sub-score decomposition is present.
        sub = body["sub_scores"]
        assert set(sub) == {
            "lane_marking_quality",
            "glare_exposure",
            "junction_complexity",
            "historical_correlation",
        }
        # Each carries a value + is_stub flag.
        for name, entry in sub.items():
            assert "value" in entry, f"sub_scores.{name} missing 'value'"
            assert "is_stub" in entry, f"sub_scores.{name} missing 'is_stub'"
            assert isinstance(entry["is_stub"], bool)

        # Glare is real; the other three are stubs.
        assert sub["glare_exposure"]["is_stub"] is False
        assert sub["lane_marking_quality"]["is_stub"] is True
        assert sub["junction_complexity"]["is_stub"] is True
        assert sub["historical_correlation"]["is_stub"] is True

    @pytest.mark.asyncio
    async def test_top_level_risk_stub_is_removed(
        self, api_client: AsyncClient, seeded_run: UUID
    ) -> None:
        resp = await api_client.get(
            f"/api/cities/cambridge/segments/{seeded_run}",
            params={"t": "2025-06-21T12:00:00Z"},
        )
        body = resp.json()
        assert "risk_stub" not in body, (
            "Top-level risk_stub should be removed in Phase 2; "
            "per-sub-score is_stub flags replace it."
        )

    @pytest.mark.asyncio
    async def test_confidence_is_present(self, api_client: AsyncClient, seeded_run: UUID) -> None:
        resp = await api_client.get(
            f"/api/cities/cambridge/segments/{seeded_run}",
            params={"t": "2025-06-21T12:00:00Z"},
        )
        body = resp.json()
        assert "confidence" in body

    @pytest.mark.asyncio
    async def test_snaps_to_nearest_hourly_sample(
        self, api_client: AsyncClient, seeded_run: UUID
    ) -> None:
        """Requesting 12:25Z should return the 12:00Z sample (nearest hour)."""
        resp_off = await api_client.get(
            f"/api/cities/cambridge/segments/{seeded_run}",
            params={"t": "2025-06-21T12:25:00Z"},
        )
        resp_on = await api_client.get(
            f"/api/cities/cambridge/segments/{seeded_run}",
            params={"t": "2025-06-21T12:00:00Z"},
        )
        assert resp_off.status_code == 200
        assert resp_on.status_code == 200
        assert resp_off.json()["sub_scores"] == resp_on.json()["sub_scores"]

    @pytest.mark.asyncio
    async def test_glare_zero_for_night_sample(
        self, api_client: AsyncClient, seeded_run: UUID
    ) -> None:
        """Pick a UTC instant where the Cambridge sun is below the
        horizon (06:00 UTC, midsummer = 02:00 EDT). Glare must be zero."""
        resp = await api_client.get(
            f"/api/cities/cambridge/segments/{seeded_run}",
            params={"t": "2025-06-21T06:00:00Z"},
        )
        assert resp.status_code == 200
        glare = resp.json()["sub_scores"]["glare_exposure"]
        assert glare["value"] == 0.0
        assert glare["is_stub"] is False

    @pytest.mark.asyncio
    async def test_glare_metadata_includes_solar_position(
        self, api_client: AsyncClient, seeded_run: UUID
    ) -> None:
        resp = await api_client.get(
            f"/api/cities/cambridge/segments/{seeded_run}",
            params={"t": "2025-06-21T16:00:00Z"},
        )
        body = resp.json()
        glare = body["sub_scores"]["glare_exposure"]
        assert "metadata" in glare
        assert "sun_azimuth_deg" in glare["metadata"]
        assert "sun_elevation_deg" in glare["metadata"]

    @pytest.mark.asyncio
    async def test_omitted_t_returns_most_recent_sample(
        self, api_client: AsyncClient, seeded_run: UUID
    ) -> None:
        """Phase-1 behavior preserved: no `t` ⇒ most recent row."""
        resp = await api_client.get(f"/api/cities/cambridge/segments/{seeded_run}")
        assert resp.status_code == 200
        body = resp.json()
        # Still returns a valid SegmentDetail shape.
        assert "sub_scores" in body
