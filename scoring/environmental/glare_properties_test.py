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

Properties (a), (b), (d) are statements about the **geometric formula**;
we test them against ``glare_from_geometry`` so pvlib's solar-position
output is not a confound. Property (c) is the integration test —
``pvlib`` produces a real negative elevation and the scorer must respect
it; we test it through the full ``glare_score`` path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from scoring.environmental.glare import (
    glare_from_geometry,
    glare_score,
    solar_position,
)

# Strategies -----------------------------------------------------------------
azimuth_strategy = st.floats(min_value=0.0, max_value=359.9999, allow_nan=False)
heading_strategy = st.floats(min_value=0.0, max_value=359.9999, allow_nan=False)
above_horizon_elev = st.floats(min_value=0.01, max_value=89.99, allow_nan=False)
low_sun_elev = st.floats(min_value=0.5, max_value=14.9, allow_nan=False)


# --- (d) zero when sun directly overhead -----------------------------------
@given(heading=heading_strategy, azimuth=azimuth_strategy)
@settings(max_examples=50, deadline=None)
def test_zero_score_when_sun_directly_overhead(heading: float, azimuth: float) -> None:
    """Property (d): elevation == 90° ⇒ value == 0 (regardless of azimuth/heading)."""
    result = glare_from_geometry(
        heading_deg=heading, sun_azimuth_deg=azimuth, sun_elevation_deg=90.0
    )
    assert result.value == 0.0


# --- (a) east-west road: glare(180 - alpha) == glare(180 + alpha) ------------------
@given(alpha=st.floats(min_value=0.0, max_value=89.0), elev=above_horizon_elev)
@settings(max_examples=100, deadline=None)
def test_symmetry_around_solar_meridian_for_east_west_road(alpha: float, elev: float) -> None:
    """Property (a): for an east-west road, glare(az = 180-alpha, elev) ==
    glare(az = 180+alpha, elev). The two azimuths are mirror images of the
    sun's path on the equinox before/after solar noon (northern
    hemisphere); on a road that runs east-west, the geometric exposure
    is identical."""
    before = glare_from_geometry(
        heading_deg=90.0, sun_azimuth_deg=180.0 - alpha, sun_elevation_deg=elev
    )
    after = glare_from_geometry(
        heading_deg=90.0, sun_azimuth_deg=180.0 + alpha, sun_elevation_deg=elev
    )
    assert before.value == pytest.approx(after.value, abs=1e-12)


# --- (a) generalized: the formula is symmetric in road-axis vs sun-azimuth -
@given(heading=heading_strategy, azimuth=azimuth_strategy, elev=above_horizon_elev)
@settings(max_examples=100, deadline=None)
def test_road_axis_symmetry_heading_vs_heading_plus_180(
    heading: float, azimuth: float, elev: float
) -> None:
    """The road *axis* — not direction — drives glare. A driver going
    heading=θ and one going heading=θ+180° see identical glare."""
    a = glare_from_geometry(heading_deg=heading, sun_azimuth_deg=azimuth, sun_elevation_deg=elev)
    h_plus_180 = (heading + 180.0) % 360.0
    b = glare_from_geometry(heading_deg=h_plus_180, sun_azimuth_deg=azimuth, sun_elevation_deg=elev)
    assert a.value == pytest.approx(b.value, abs=1e-12)


# --- (b) glare strictly increases as elevation drops (alignment fixed) -----
@given(
    heading=heading_strategy,
    azimuth=azimuth_strategy,
    elev_low=low_sun_elev,
    elev_high=low_sun_elev,
)
@settings(max_examples=100, deadline=None)
def test_glare_monotonic_in_elevation_below_15deg(
    heading: float, azimuth: float, elev_low: float, elev_high: float
) -> None:
    """Property (b): with alignment held fixed, lower elevation → at
    least as much glare. We use the pure geometric formula so the only
    independent variable is elevation; the alignment factor cancels
    completely from the comparison.

    "At least as much" rather than "strictly more": if alignment is
    exactly 0 (sun perpendicular to road axis), both samples are 0 and
    the inequality is satisfied as equality."""
    assume(elev_low < elev_high)
    g_low = glare_from_geometry(
        heading_deg=heading, sun_azimuth_deg=azimuth, sun_elevation_deg=elev_low
    )
    g_high = glare_from_geometry(
        heading_deg=heading, sun_azimuth_deg=azimuth, sun_elevation_deg=elev_high
    )
    assert g_low.value >= g_high.value, (
        f"Lower elevation should not produce less glare: "
        f"elev_low={elev_low:.3f}, elev_high={elev_high:.3f}, "
        f"g_low={g_low.value:.6f}, g_high={g_high.value:.6f}"
    )


# --- (c) zero when sun below horizon — integration through pvlib ----------
@given(
    lat=st.floats(min_value=-60.0, max_value=60.0),
    lon=st.floats(min_value=-180.0, max_value=180.0),
    heading=heading_strategy,
)
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_zero_score_when_sun_below_horizon(lat: float, lon: float, heading: float) -> None:
    """Property (c): elevation < 0° ⇒ value == 0.

    Picks a winter-night moment at the longitude where the sun is
    virtually guaranteed to be down: local midnight, December 21.
    """
    local_midnight_utc = datetime(2025, 12, 21, 0, 0, tzinfo=UTC) - timedelta(hours=lon / 15.0)
    _, elev = solar_position(lat=lat, lon=lon, at=local_midnight_utc)
    assume(elev < 0.0)
    result = glare_score(heading_deg=heading, lat=lat, lon=lon, at=local_midnight_utc)
    assert result.value == 0.0, (
        f"elev={elev:.2f}°, lat={lat}, lon={lon} — expected 0, got {result.value}"
    )


# --- Sanity: range bounds hold for any geometric inputs --------------------
@given(heading=heading_strategy, azimuth=azimuth_strategy, elev=above_horizon_elev)
@settings(max_examples=100, deadline=None)
def test_value_in_unit_interval(heading: float, azimuth: float, elev: float) -> None:
    """Pydantic validates this too, but a property test is a stronger
    statement than a one-shot pydantic check."""
    result = glare_from_geometry(
        heading_deg=heading, sun_azimuth_deg=azimuth, sun_elevation_deg=elev
    )
    assert 0.0 <= result.value <= 1.0
