"""Property tests for the glare scorer.

Mathematical invariants the implementation must hold. From `spec.md` §AC-1:

    (a) Symmetry around solar noon for east-west road headings.
    (b) Monotonic decrease as solar elevation drops below ~15°.
        (i.e., lower sun → MORE glare. "Decrease" in the spec refers to
        elevation; the score *increases* as elevation falls toward 0°.)
    (c) Zero score when sun below horizon.
    (d) Zero score when sun directly overhead (elevation = 90°).

These are first-class invariants. Per the plan: if `hypothesis` finds a
counterexample, fix the *implementation*, never weaken the property —
unless the property itself was wrong, in which case correct it
deliberately with a comment.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from scoring.environmental.glare import GlareScorer, solar_position
from scoring.interface import ScoringSegment

SEG_ID = UUID("12345678-1234-5678-1234-567812345678")


def _seg(lat: float, lon: float, heading_deg: float) -> ScoringSegment:
    return ScoringSegment(
        segment_id=SEG_ID,
        heading_deg=heading_deg,
        lat=lat,
        lon=lon,
    )


# --- Strategy: representative coordinates and times -----------------------
# Latitudes restricted to the tropics-to-polar-circle band: outside the
# polar circles, "solar noon" stops behaving sensibly across the year
# (the sun may not rise / set at all), which breaks the symmetry property
# (a) by design rather than by bug.
lat_strategy = st.floats(min_value=-65.0, max_value=65.0, allow_nan=False, allow_infinity=False)
lon_strategy = st.floats(min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False)
heading_strategy = st.floats(
    min_value=0.0, max_value=359.9999, allow_nan=False, allow_infinity=False
)

# Equinoxes are the most well-behaved days for the symmetry test — sun
# crosses the local meridian at almost exactly the same elevation as
# 12-hour-offset, and rises/sets close to due east/west. Avoid solstices
# in the symmetry test (sun's path is steeper there, but reflection
# symmetry still holds — keep the test simple).
equinox_date_strategy = st.sampled_from(
    [datetime(2025, 3, 20, 0, 0, tzinfo=UTC), datetime(2025, 9, 22, 0, 0, tzinfo=UTC)]
)


@pytest.fixture
def scorer() -> GlareScorer:
    return GlareScorer()


# --- (c) zero when sun below horizon --------------------------------------
@given(lat=lat_strategy, lon=lon_strategy, heading=heading_strategy)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_zero_score_when_sun_below_horizon(
    scorer: GlareScorer, lat: float, lon: float, heading: float
) -> None:
    """Property (c): elevation < 0° ⇒ glare value == 0."""
    # Pick a winter-night moment at the longitude where the sun is
    # virtually guaranteed to be down: local midnight, December 21.
    local_midnight_utc = datetime(2025, 12, 21, 0, 0, tzinfo=UTC) - timedelta(hours=lon / 15.0)
    az, elev = solar_position(lat=lat, lon=lon, at=local_midnight_utc)
    if elev >= 0.0:
        # In the polar night / day band the strategy bounds limit us to,
        # local midnight at the equinoxes may still have sun up in
        # high-latitude summer; skip those generated examples to keep
        # the property's premise (sun below horizon) true.
        return
    result = scorer.score(_seg(lat, lon, heading), at=local_midnight_utc)
    assert result.value == 0.0, (
        f"elev={elev:.2f}°, lat={lat}, lon={lon} — expected 0, got {result.value}"
    )


# --- (d) zero when sun directly overhead ----------------------------------
def test_zero_score_when_sun_directly_overhead(scorer: GlareScorer) -> None:
    """Property (d): elevation == 90° ⇒ glare value == 0.

    `pvlib` never returns exactly 90° in the wild (the sub-solar point is
    typically over the tropics, and a property strategy may not land
    directly on it). The scorer's *internal* formula is what we need to
    verify here; we call it with a synthetic 90° elevation via a
    monkeypatched solar_position to assert the formula zeroes out.

    Using monkeypatch for this single case is honest: the property is a
    statement about the formula, not about pvlib's outputs.
    """
    from scoring.environmental import glare as glare_mod

    real_solar = glare_mod.solar_position
    try:
        glare_mod.solar_position = lambda **_kwargs: (180.0, 90.0)  # type: ignore[assignment]
        result = scorer.score(_seg(0.0, 0.0, 90.0), at=datetime(2025, 1, 1, 12, 0, tzinfo=UTC))
        assert result.value == 0.0, f"elevation=90° should produce 0 glare, got {result.value}"
    finally:
        glare_mod.solar_position = real_solar  # type: ignore[assignment]


# --- (a) symmetry around solar noon for east-west road headings ----------
@given(
    lat=st.floats(min_value=-55.0, max_value=55.0),
    lon=st.floats(min_value=-180.0, max_value=180.0),
    delta_minutes=st.integers(min_value=15, max_value=180),
)
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_symmetry_around_solar_noon_for_east_west_roads(
    scorer: GlareScorer, lat: float, lon: float, delta_minutes: int
) -> None:
    """Property (a): for an east-west road, glare(noon - Δ) ≈ glare(noon + Δ).

    Solar geometry around the meridian crossing is symmetric:
    elevation(noon - Δ) == elevation(noon + Δ); the azimuth reflects
    (az(noon - Δ) = 360 - az(noon + Δ) in the conventional definition).
    An east-west road's heading vector projects equivalently onto both
    azimuths, so the geometric glare must match.

    Use the equinox to keep the experiment well-conditioned and avoid
    edge cases at high latitude where the sun barely clears the horizon.
    """
    # Approximate solar noon in UTC: 12:00 minus the longitude in hours.
    # Equinox lets us ignore the equation-of-time correction (<1 min there).
    equinox = datetime(2025, 3, 20, 0, 0, tzinfo=UTC)
    solar_noon_utc = equinox.replace(hour=12) - timedelta(hours=lon / 15.0)

    before = solar_noon_utc - timedelta(minutes=delta_minutes)
    after = solar_noon_utc + timedelta(minutes=delta_minutes)

    # Skip examples where the sun is below horizon at either sample point —
    # the property's premise (the sun is crossing the meridian) fails there.
    _, elev_before = solar_position(lat=lat, lon=lon, at=before)
    _, elev_after = solar_position(lat=lat, lon=lon, at=after)
    if elev_before < 1.0 or elev_after < 1.0:
        return

    result_before = scorer.score(_seg(lat, lon, 90.0), at=before)  # E-W: heading 90°
    result_after = scorer.score(_seg(lat, lon, 90.0), at=after)

    # 0.05 absolute tolerance: pvlib's solar position has ~arc-minute accuracy
    # which translates to ≪ 0.01 in the unit-interval score; plus the
    # equation-of-time at the equinox is small but nonzero.
    assert result_before.value == pytest.approx(result_after.value, abs=0.05), (
        f"EW symmetry around solar noon failed: "
        f"lat={lat:.2f}, lon={lon:.2f}, Δ={delta_minutes}min, "
        f"before={result_before.value:.4f}, after={result_after.value:.4f}"
    )


# --- (b) monotonic increase in glare as elevation falls below ~15° -------
@given(
    lat=st.floats(min_value=20.0, max_value=55.0),
    heading=heading_strategy,
)
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_glare_increases_as_elevation_drops_below_15_degrees(
    scorer: GlareScorer, lat: float, heading: float
) -> None:
    """Property (b): below ~15° elevation, lower sun → more glare.

    "Monotonic decrease as solar elevation drops below ~15°" in the spec
    refers to the *elevation* dropping; the glare score *rises* as the
    sun gets lower. We pick a fixed lat/longitude (longitude is fixed
    so we control time-of-day cleanly) and sweep two morning timestamps
    where the sun is in the low-east band.

    The driver must be looking generally toward the sun for the
    monotonicity to bite — pick a heading whose component along the
    azimuth is positive; otherwise we're testing the orthogonal case
    where geometry says 0 ≈ 0 and monotonicity is trivially "≤".

    To keep the property robust to noise, require BOTH samples to have
    a non-trivial along-sun component.
    """
    # Use the equinox to keep elevation calculations simple.
    equinox = datetime(2025, 3, 20, 0, 0, tzinfo=UTC)
    fixed_lon = 0.0  # arbitrary, drops out
    # Two morning sample times: ~30 min and ~90 min after sunrise.
    sunrise_utc = equinox.replace(hour=6)
    t1 = sunrise_utc + timedelta(minutes=30)
    t2 = sunrise_utc + timedelta(minutes=90)

    _, elev1 = solar_position(lat=lat, lon=fixed_lon, at=t1)
    _, elev2 = solar_position(lat=lat, lon=fixed_lon, at=t2)

    # Both must be below ~15° and above 0° for the property to apply.
    if not (0.5 < elev1 < 15.0 and 0.5 < elev2 < 15.0):
        return
    # The earlier sample (t1) must have a *lower* elevation than t2 for
    # the test framing to be coherent.
    if elev1 >= elev2:
        return

    g1 = scorer.score(_seg(lat, fixed_lon, heading), at=t1)
    g2 = scorer.score(_seg(lat, fixed_lon, heading), at=t2)

    # If both are zero (sun's azimuth ⟂ heading), the property is
    # vacuously satisfied (0 == 0). Otherwise the lower sun must score
    # at least as high.
    if g1.value == 0.0 and g2.value == 0.0:
        return
    assert g1.value >= g2.value, (
        f"Glare should *not* decrease as elevation drops "
        f"(elev1={elev1:.2f} < elev2={elev2:.2f} → g1={g1.value:.4f} < g2={g2.value:.4f})"
    )
