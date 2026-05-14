"""GET /segments/{id} — Phase 1 + Phase 2 shape.

Asserts the explainability invariant: all four sub-score fields are
present. Phase 1's top-level ``risk_stub`` is removed in Phase 2 (spec
§Technical Note 7); per-sub-score ``is_stub`` flags inside ``sub_scores``
replace it.

When no scoring run has executed for a freshly-ingested segment, the
endpoint falls back to the Phase 1 stub (``stub_risk``) so the response
shape stays valid; in that path every ``is_stub`` is true.
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
    """No scoring run has executed in this fixture — falls back to stub."""
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

    # Phase 2 breaking change: top-level risk_stub flag removed.
    assert "risk_stub" not in body

    # Stub fallback: every sub-score carries is_stub=true.
    for name, entry in sub.items():
        assert entry["is_stub"] is True, f"sub_scores.{name} should be stub when no run executed"

    # Confidence is present even though Phase 2 stubs it for the no-run path.
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
    """stub_risk fallback is a pure function — two calls return the same payload."""
    a = (await api_client.get(f"/segments/{seed_segment}")).json()
    b = (await api_client.get(f"/segments/{seed_segment}")).json()
    assert a["composite_risk"] == b["composite_risk"]
    assert a["sub_scores"] == b["sub_scores"]
