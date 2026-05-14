"""GET /segments/{id} — Phase 3 (API 3.0) shape.

Asserts the breaking API 3.0 changes:

- ``confidence`` is an object with ``value`` (float) + ``limiter``
  (one of "freshness" | "coverage" | "model"). The scalar shape from
  Phase 2 is absent.
- ``imagery`` is an array. When `segment_imagery` has rows for the
  segment, every entry includes a pre-signed MinIO URL that responds
  200/HEAD.
- ``lane_marking_quality.metadata`` carries ``model_uncertainty``
  when ``is_stub=false`` (a real perception run wrote the row).
- Junction + historical sub-scores remain stubbed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import httpx
import psycopg
import pytest
from httpx import AsyncClient
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration


def _seed_real_score_for(
    owner_conn: psycopg.Connection[Any],
    segment_id: UUID,
    *,
    composite: float = 0.42,
    scalar_confidence: float = 0.8,
) -> None:
    """Insert a `segment_scores` row with real glare + lane_marking."""
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO scoring_runs (
                id,
                scoring_run_timestamp,
                perception_model_version,
                osm_snapshot_date,
                imagery_capture_window,
                propagation_algorithm_version,
                notes
            )
            VALUES (
                gen_random_uuid(),
                now(),
                'lane-marking-standin-deadbeef',
                '2026-05-13',
                '[2025-06-01,2025-09-01)'::daterange,
                'none-phase-2',
                'phase3-api-test'
            )
            RETURNING id, scoring_run_timestamp
            """,
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
            )
            VALUES (
                %s, %s, 0.0,
                0.55, 0.30,
                0.0, 0.0,
                %s,
                false, false,
                true, true,
                %s, %s,
                'lane-marking-standin-deadbeef', '2026-05-13',
                '[2025-06-01,2025-09-01)'::daterange, 'none-phase-2'
            )
            """,
            (segment_id, composite, scalar_confidence, run_id, run_ts),
        )
    owner_conn.commit()


def _seed_imagery_for(
    owner_conn: psycopg.Connection[Any], segment_id: UUID, *, count: int = 3
) -> None:
    """Insert N segment_imagery rows + corresponding MinIO objects."""
    import io
    import os
    from pathlib import Path

    from minio import Minio

    client = Minio(
        os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.environ.get("MINIO_ROOT_USER", "streetsense"),
        secret_key=os.environ.get("MINIO_ROOT_PASSWORD", "streetsense"),
        secure=False,
    )
    if not client.bucket_exists("streetsense-imagery"):
        client.make_bucket("streetsense-imagery")

    # Use the committed fixture PNG as the object body — guarantees a
    # real image that MinIO accepts and that a downstream image-reader
    # can decode.
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "perception"
        / "images"
        / "01_obvious_lane_markings.png"
    )
    image_bytes = fixture_path.read_bytes()
    with owner_conn.cursor() as cur:
        for i in range(count):
            provider_image_id = f"phase3-test-{segment_id}-{i}"
            object_key = f"mapillary/{provider_image_id}.jpg"
            try:
                client.stat_object("streetsense-imagery", object_key)
            except Exception:
                client.put_object(
                    "streetsense-imagery",
                    object_key,
                    io.BytesIO(image_bytes),
                    length=len(image_bytes),
                    content_type="image/png",
                )
            cur.execute(
                """
                INSERT INTO segment_imagery (
                    segment_id, provider, provider_image_id, sample_index,
                    capture_date, heading_deg, camera_params, object_key
                )
                VALUES (%s, 'mapillary', %s, %s, %s, %s, %s, %s)
                ON CONFLICT (provider, provider_image_id, segment_id) DO NOTHING
                """,
                (
                    segment_id,
                    provider_image_id,
                    i,
                    date(2025, 7, 15),
                    90.0,
                    Jsonb({"thumb_1024_url": f"https://example/{provider_image_id}"}),
                    object_key,
                ),
            )
    owner_conn.commit()


@pytest.fixture(autouse=True)
def _clean_imagery(owner_conn: psycopg.Connection[Any]) -> None:
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_imagery")
    owner_conn.commit()


@pytest.mark.asyncio
async def test_confidence_is_object_with_limiter_and_value(
    api_client: AsyncClient,
    owner_conn: psycopg.Connection[Any],
    seed_segment: UUID,
) -> None:
    _seed_real_score_for(owner_conn, seed_segment)
    _seed_imagery_for(owner_conn, seed_segment, count=3)

    resp = await api_client.get(f"/segments/{seed_segment}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Phase 3 breaking shape: confidence is an OBJECT, not a float.
    confidence = body["confidence"]
    assert isinstance(confidence, dict), (
        f"Phase 3 confidence must be an object; got scalar {confidence!r}"
    )
    assert 0.0 <= confidence["value"] <= 1.0
    assert confidence["limiter"] in {"freshness", "coverage", "model"}


@pytest.mark.asyncio
async def test_imagery_array_with_pre_signed_urls(
    api_client: AsyncClient,
    owner_conn: psycopg.Connection[Any],
    seed_segment: UUID,
) -> None:
    _seed_real_score_for(owner_conn, seed_segment)
    _seed_imagery_for(owner_conn, seed_segment, count=3)

    resp = await api_client.get(f"/segments/{seed_segment}")
    body = resp.json()
    imagery = body["imagery"]
    assert isinstance(imagery, list)
    assert len(imagery) == 3
    for entry in imagery:
        assert entry["provider"] == "mapillary"
        # capture_date round-trips as a string.
        assert isinstance(entry["capture_date"], str)
        # Pre-signed URL responds to a GET. `presigned_get_object`
        # signs the method, so HEAD against the same URL fails 403.
        assert entry["url"].startswith("http")
        async with httpx.AsyncClient() as outer:
            got = await outer.get(entry["url"], timeout=5.0)
        assert got.status_code == 200, (
            f"Pre-signed URL did not GET 200: {got.status_code} {entry['url']}"
        )
        assert len(got.content) > 0


@pytest.mark.asyncio
async def test_lane_marking_metadata_carries_model_uncertainty(
    api_client: AsyncClient,
    owner_conn: psycopg.Connection[Any],
    seed_segment: UUID,
) -> None:
    _seed_real_score_for(owner_conn, seed_segment)
    _seed_imagery_for(owner_conn, seed_segment, count=2)

    resp = await api_client.get(f"/segments/{seed_segment}")
    body = resp.json()
    lane = body["sub_scores"]["lane_marking_quality"]
    assert lane["is_stub"] is False
    assert "model_uncertainty" in lane["metadata"]
    assert 0.0 <= lane["metadata"]["model_uncertainty"] <= 1.0


@pytest.mark.asyncio
async def test_no_imagery_yields_coverage_limited_confidence(
    api_client: AsyncClient,
    owner_conn: psycopg.Connection[Any],
    seed_segment: UUID,
) -> None:
    """A segment with zero `segment_imagery` rows → confidence.limiter == 'coverage'."""
    _seed_real_score_for(owner_conn, seed_segment)
    # No _seed_imagery_for call.

    resp = await api_client.get(f"/segments/{seed_segment}")
    body = resp.json()
    assert body["confidence"]["value"] == 0.0
    assert body["confidence"]["limiter"] == "coverage"
    assert body["imagery"] == []


@pytest.mark.asyncio
async def test_t_parameter_still_snaps(
    api_client: AsyncClient,
    owner_conn: psycopg.Connection[Any],
    seed_segment: UUID,
) -> None:
    """Regression: Phase 2's t=... parameter still works in API 3.0."""
    _seed_real_score_for(owner_conn, seed_segment)
    _seed_imagery_for(owner_conn, seed_segment, count=1)

    resp = await api_client.get(
        f"/segments/{seed_segment}",
        params={"t": datetime.now(UTC).isoformat()},
    )
    assert resp.status_code == 200
    assert "confidence" in resp.json()
