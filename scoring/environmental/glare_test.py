"""Fixed-value reference tests for the glare scorer.

These are smoke tests on geometrically meaningful scenarios. The
*correctness* invariants live in `glare_properties_test.py`; these tests
exist so a future regression on the headline numbers fires immediately.

A note on UTC times: Cambridge, MA (~71.1°W) is UTC-5 / UTC-4 (EDT). Solar
noon on the summer solstice falls at ~16:50 UTC, not 12:00 UTC. The
scenarios below pick UTC times that produce the *geometric situation*
described (sun overhead vs. low east vs. below horizon), rather than
local-clock convention. See the comments inline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from scoring.environmental.glare import GlareScorer
from scoring.interface import ScoringSegment

# Cambridge, MA — same coordinates the seed fixture uses for the demo.
CAMBRIDGE_LAT = 42.3736
CAMBRIDGE_LON = -71.1097

# Stable UUID so failures point at a recognizable segment.
SEG_ID = UUID("12345678-1234-5678-1234-567812345678")


def _seg(heading_deg: float) -> ScoringSegment:
    return ScoringSegment(
        segment_id=SEG_ID,
        heading_deg=heading_deg,
        lat=CAMBRIDGE_LAT,
        lon=CAMBRIDGE_LON,
    )


@pytest.fixture
def scorer() -> GlareScorer:
    return GlareScorer()


class TestSummerSolsticeSolarNoon:
    """At solar noon on the summer solstice the sun is nearly overhead at
    Cambridge (declination +23.4°, latitude 42.4° → elevation ~71°). High
    elevation drives the "sun directly overhead → no geometric glare"
    factor; for any road heading the score should be modest (well under
    the morning low-sun case)."""

    AT = datetime(2025, 6, 21, 16, 50, tzinfo=UTC)  # ~solar noon Cambridge

    def test_east_west_road_at_solar_noon_is_modest(self, scorer: GlareScorer) -> None:
        result = scorer.score(_seg(heading_deg=90.0), at=self.AT)
        assert result.is_stub is False
        assert 0.0 <= result.value <= 0.5, (
            f"Solar-noon glare on EW road should be modest; got {result.value}"
        )
        # The sun is up — confidence in the geometric calc is full.
        assert result.confidence == pytest.approx(1.0)
        assert "sun_azimuth_deg" in result.metadata
        assert "sun_elevation_deg" in result.metadata
        elev = float(result.metadata["sun_elevation_deg"])
        assert elev > 60.0, f"Solar noon at summer solstice → elevation ≫ 60°; got {elev}"


class TestSummerSolsticeMorningLowSun:
    """An hour or so after sunrise on the summer solstice, the sun sits
    low in the east. A driver headed due east faces almost directly into
    the sun: the canonical "glare exposure" geometry. Expect a HIGH score
    that exceeds the solar-noon case for the same heading."""

    AT = datetime(2025, 6, 21, 10, 30, tzinfo=UTC)  # ~06:30 EDT

    def test_east_facing_road_at_morning_low_sun_is_high(self, scorer: GlareScorer) -> None:
        result = scorer.score(_seg(heading_deg=90.0), at=self.AT)
        assert result.is_stub is False
        assert result.value > 0.6, (
            f"Morning low-sun glare on E-facing road should be high; got {result.value}"
        )
        elev = float(result.metadata["sun_elevation_deg"])
        assert 0.0 < elev < 30.0, f"Sun should be low in the east; elevation={elev}"

    def test_morning_exceeds_noon_for_east_heading(self, scorer: GlareScorer) -> None:
        """Relative ordering: low-east-sun morning > overhead-sun noon."""
        morning = scorer.score(_seg(heading_deg=90.0), at=self.AT)
        noon = scorer.score(
            _seg(heading_deg=90.0),
            at=TestSummerSolsticeSolarNoon.AT,
        )
        assert morning.value > noon.value


class TestSunBelowHorizon:
    """Winter solstice, deep night UTC at Cambridge — sun is far below
    the horizon. Glare is exactly zero regardless of road heading."""

    AT = datetime(2025, 12, 21, 6, 0, tzinfo=UTC)  # 01:00 EST — middle of the night

    @pytest.mark.parametrize("heading", [0.0, 45.0, 90.0, 180.0, 270.0])
    def test_glare_is_zero_for_any_heading_when_sun_below_horizon(
        self, scorer: GlareScorer, heading: float
    ) -> None:
        result = scorer.score(_seg(heading_deg=heading), at=self.AT)
        assert result.value == 0.0
        elev = float(result.metadata["sun_elevation_deg"])
        assert elev < 0.0, f"Expected sun below horizon at AT; got elevation={elev}"


class TestScorerProtocolFields:
    """Smoke tests on the scorer's contract (the `SubScorer` Protocol)."""

    def test_name_is_glare(self, scorer: GlareScorer) -> None:
        assert scorer.name == "glare"

    def test_result_is_pydantic_subscoreresult(self, scorer: GlareScorer) -> None:
        result = scorer.score(_seg(heading_deg=0.0), at=TestSunBelowHorizon.AT)
        # SubScoreResult is frozen — assignment must raise.
        from pydantic import ValidationError

        with pytest.raises((TypeError, ValidationError)):
            result.value = 0.5
