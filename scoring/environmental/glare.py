"""Glare scorer — environmental sub-score driven by solar geometry.

Pure-functional. Same inputs → same output. No I/O, no module-level
caches keyed on inputs, no `datetime.now()`. Determinism is enforced by
the property tests in `glare_properties_test.py`.

The geometry, in one paragraph:

The driver-facing road vector ``r̂`` and the sun's azimuth vector ``ŝ``
both live in the local horizontal plane. The acute angle between the
road *axis* and the sun *azimuth* (collapsed onto [0°, 90°] so that
driving direction is irrelevant — a road and its reverse experience the
same glare) determines an alignment factor ``cos²(angle)``: 1 when the
sun is dead ahead, 0 when the sun is exactly to the side. We then
attenuate by an elevation factor ``1 - sin(elev)``: 1 at the horizon, 0
at the zenith. The combined value is ``alignment * elev_factor``,
clamped to [0, 1]. Below the horizon (``elev < 0``) the score is
hard-zeroed.

Choice of elevation attenuator ``1 - sin(elev)`` (rather than
``cos(elev)``): both go from 1 at horizon to 0 at zenith, but
``1 - sin(elev)`` is markedly more sensitive in the 0-15 degree band where
real driver-glare is most pronounced, and it equals **exactly** zero at
``elev = 90°`` under IEEE 754 (``math.sin(math.radians(90)) == 1.0``),
preserving the spec's "zero at zenith" invariant without a special
case. ``cos(math.radians(90))`` is ~6e-17, not zero.

References:
    - Solar position: `pvlib.solarposition.get_solarposition`, NREL SPA
      ([ADR 0003](../../docs/adr/0003-solar-position-library.md)).
    - Heading convention: degrees clockwise from true north
      ([scoring/interface.py](../interface.py)).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

import pandas as pd
import pvlib

from scoring.interface import ScoringSegment, SubScoreResult


def solar_position(*, lat: float, lon: float, at: datetime) -> tuple[float, float]:
    """Compute (azimuth, apparent elevation) in degrees at ``(lat, lon)`` at UTC ``at``.

    Wraps `pvlib.solarposition.get_solarposition` behind a plain-tuple
    interface so the rest of the codebase does not import pandas.

    Azimuth follows pvlib's convention: degrees clockwise from true north
    (0 = north, 90 = east). Elevation is the refraction-corrected
    "apparent" elevation — the angle the sun appears at to a ground
    observer, including atmospheric refraction near the horizon. This is
    what a camera sees, which is what glare cares about.
    """
    if at.tzinfo is None:
        raise ValueError("`at` must be timezone-aware (UTC)")
    times = pd.DatetimeIndex([at])
    sp = pvlib.solarposition.get_solarposition(times, latitude=lat, longitude=lon)
    azimuth_deg = float(sp["azimuth"].iloc[0])
    elevation_deg = float(sp["apparent_elevation"].iloc[0])
    return azimuth_deg, elevation_deg


def solar_position_many(
    *, lat: float, lon: float, ats: Sequence[datetime]
) -> list[tuple[float, float]]:
    """Vectorized variant of `solar_position` — one pvlib call for many
    timestamps at a single (lat, lon).

    The per-call overhead of `pvlib.solarposition.get_solarposition`
    dominates the cost for city-scale scoring runs (~36k segments x 24
    hourly samples = ~860k calls). Batching by lat/lon collapses that to
    one call per segment.
    """
    if not ats:
        return []
    for at in ats:
        if at.tzinfo is None:
            raise ValueError("every `at` must be timezone-aware (UTC)")
    times = pd.DatetimeIndex(list(ats))
    sp = pvlib.solarposition.get_solarposition(times, latitude=lat, longitude=lon)
    azimuths = sp["azimuth"].tolist()
    elevations = sp["apparent_elevation"].tolist()
    return [(float(az), float(el)) for az, el in zip(azimuths, elevations, strict=True)]


def _angle_between_road_axis_and_sun_azimuth(heading_deg: float, azimuth_deg: float) -> float:
    """Return the acute angle (in degrees, 0-90) between the road *axis* and the sun azimuth.

    A road's "axis" is a line, not a ray — a driver going due east and
    a driver going due west share the same glare exposure as the same
    road in the same sun. So we collapse the heading-vs-azimuth angle
    onto [0, 90]: parallel = 0°, perpendicular = 90°.
    """
    delta = abs(heading_deg - azimuth_deg) % 360.0
    if delta > 180.0:
        delta = 360.0 - delta  # collapse onto [0, 180]
    if delta > 90.0:
        delta = 180.0 - delta  # collapse onto [0, 90] (road-axis symmetry)
    return delta


def glare_from_geometry(
    *, heading_deg: float, sun_azimuth_deg: float, sun_elevation_deg: float
) -> SubScoreResult:
    """Pure geometric formula. Exposed so property tests can hold the
    inputs fixed (no pvlib coupling)."""
    if sun_elevation_deg < 0.0:
        return SubScoreResult(
            value=0.0,
            confidence=1.0,
            is_stub=False,
            metadata={
                "sun_azimuth_deg": sun_azimuth_deg,
                "sun_elevation_deg": sun_elevation_deg,
            },
        )

    angle_deg = _angle_between_road_axis_and_sun_azimuth(heading_deg, sun_azimuth_deg)
    cos_angle = math.cos(math.radians(angle_deg))
    alignment = cos_angle * cos_angle  # in [0, 1]

    # Exact-zero-at-zenith elevation attenuator. See module docstring.
    elev_factor = 1.0 - math.sin(math.radians(sun_elevation_deg))  # in [0, 1] for elev in [0, 90]

    value = max(0.0, min(1.0, alignment * elev_factor))

    return SubScoreResult(
        value=value,
        confidence=1.0,
        is_stub=False,
        metadata={
            "sun_azimuth_deg": sun_azimuth_deg,
            "sun_elevation_deg": sun_elevation_deg,
        },
    )


def glare_score(*, heading_deg: float, lat: float, lon: float, at: datetime) -> SubScoreResult:
    """Compute the glare sub-score for a road segment at a UTC instant.

    Thin wrapper that resolves the solar position then delegates to
    ``glare_from_geometry``. Confidence is set to 1.0 — the geometric
    calculation is exact; real confidence assembly (data freshness +
    coverage + model uncertainty) arrives in Phase 3+ per spec §"Out of
    Scope". ``is_stub`` is False — this is the first real sub-score.
    """
    azimuth_deg, elevation_deg = solar_position(lat=lat, lon=lon, at=at)
    return glare_from_geometry(
        heading_deg=heading_deg,
        sun_azimuth_deg=azimuth_deg,
        sun_elevation_deg=elevation_deg,
    )


class GlareScorer:
    """`SubScorer` Protocol implementation for environmental glare.

    Stateless. Holding a single shared instance is safe.
    """

    name: str = "glare"

    def score(self, segment: ScoringSegment, *, at: datetime) -> SubScoreResult:
        return glare_score(
            heading_deg=segment.heading_deg,
            lat=segment.lat,
            lon=segment.lon,
            at=at,
        )

    def score_for_samples(
        self, segment: ScoringSegment, *, ats: Sequence[datetime]
    ) -> list[SubScoreResult]:
        """Vectorized: one pvlib call per segment, geometric formula
        per (segment, sample). 20-30x faster than ``score`` in a loop
        at city scale because ``pvlib.solarposition`` has dominant
        per-call setup cost.
        """
        positions = solar_position_many(lat=segment.lat, lon=segment.lon, ats=ats)
        return [
            glare_from_geometry(
                heading_deg=segment.heading_deg,
                sun_azimuth_deg=az,
                sun_elevation_deg=el,
            )
            for az, el in positions
        ]


__all__ = [
    "GlareScorer",
    "glare_from_geometry",
    "glare_score",
    "solar_position",
    "solar_position_many",
]
