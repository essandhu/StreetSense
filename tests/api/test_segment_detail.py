"""GET /segments/{id} — Task 1.5.2.

Asserts the explainability invariant from day one:
all four sub-score fields are present and `risk_stub` is true.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_segment_detail_returns_200_with_all_sub_scores(
    api_client: AsyncClient, seed_segment: UUID
) -> None:
    resp = await api_client.get(f"/segments/{seed_segment}")
    assert resp.status_code == 200, resp.text

    body = resp.json()

    # Composite risk + the *four* sub-score fields present.
    assert "composite_risk" in body
    sub = body["sub_scores"]
    assert set(sub.keys()) == {
        "lane_marking_quality",
        "glare_exposure",
        "junction_complexity",
        "historical_correlation",
    }

    # Phase 1 always returns stub values.
    assert body["risk_stub"] is True

    # Confidence is present even though Phase 1 stubs it.
    assert "confidence" in body

    # Branded UUID echoes back as a parseable UUID string.
    UUID(body["segment_id"])  # raises if invalid


@pytest.mark.asyncio
async def test_unknown_segment_returns_404(api_client: AsyncClient) -> None:
    bogus = uuid4()
    resp = await api_client.get(f"/segments/{bogus}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_segment_detail_is_deterministic_across_calls(
    api_client: AsyncClient, seed_segment: UUID
) -> None:
    """stub_risk is a pure function — two calls return the same score."""
    a = (await api_client.get(f"/segments/{seed_segment}")).json()
    b = (await api_client.get(f"/segments/{seed_segment}")).json()
    assert a["composite_risk"] == b["composite_risk"]
    assert a["sub_scores"] == b["sub_scores"]
