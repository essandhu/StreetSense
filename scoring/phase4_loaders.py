"""PostGIS-backed loaders for the Phase 4 scorers.

The Phase 4 scorers are decoupled from data sources by callable seams:

  - :class:`scoring.junction.scorer.JunctionComplexityScorer` consumes
    a ``TopologyLoader`` ``Callable[[UUID], SegmentTopology]``.
  - :class:`scoring.historical.scorer.HistoricalCorrelationScorer`
    consumes an ``IncidentLoader`` ``Callable[[ScoringSegment, float],
    Sequence[IncidentNearby]]``.

Production runs bind those callables to PostGIS queries; unit tests
bind to in-memory fixtures. This module owns the PostGIS bindings.

Both loaders are **eager**: they prefetch the relevant data into
in-memory dicts at construction time. The Phase 4 scoring run
processes every segment x hour, so per-call DB roundtrips would
dominate wall-clock; a single batched load is materially faster and
the memory footprint is bounded (Cambridge: ~36k segments x ~100B
metadata + 3.6k incidents x ~100B = a few MB).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

import psycopg

from ingestion.incidents.provider import IncidentSeverity
from scoring.historical.scorer import IncidentNearby
from scoring.interface import ScoringSegment
from scoring.junction.scorer import (
    JunctionEndpoint,
    SegmentTopology,
)

# Pull all the data the junction scorer needs in one query:
# - segment id, lane_count (attrs->>'lanes'), road_class (attrs->>'highway')
# - start/end snapped to a 1e-6° grid for endpoint matching
# - segment length in meters (for future weighting; not used today)
_LOAD_TOPOLOGY_SQL: Final[str] = """
SELECT
    id,
    COALESCE(NULLIF(attrs->>'lanes', '')::int, 2) AS lane_count,
    COALESCE(NULLIF(attrs->>'highway', ''), 'unclassified') AS road_class,
    ST_AsText(ST_SnapToGrid(ST_StartPoint(geometry), 0.000001)) AS start_key,
    ST_AsText(ST_SnapToGrid(ST_EndPoint(geometry),   0.000001)) AS end_key,
    -- Start tangent: bearing of the first edge of the line.
    degrees(ST_Azimuth(ST_StartPoint(geometry), ST_PointN(geometry, 2))) AS start_bearing,
    -- End tangent: bearing of the last edge of the line (reversed from
    -- the convention because we want the bearing *into* the endpoint).
    degrees(
        ST_Azimuth(
            ST_PointN(geometry, ST_NumPoints(geometry) - 1),
            ST_EndPoint(geometry)
        )
    ) AS end_bearing
FROM road_segments
"""


@dataclass(frozen=True, slots=True)
class _RawSegmentRow:
    segment_id: UUID
    lane_count: int
    road_class: str
    start_key: str
    end_key: str
    start_bearing_deg: float
    end_bearing_deg: float


def _safe_int(value: Any, default: int = 2) -> int:
    try:
        if value is None:
            return default
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def _angle_between_bearings(a: float, b: float) -> float:
    """Minimum angular difference between two compass bearings, in degrees.

    Returns a value in (0, 180]. 90° = perpendicular, < 30° = sharp
    merge. Used by :class:`JunctionComplexityScorer` to score the
    merge-angle signal.
    """
    diff = abs(a - b) % 360.0
    if diff > 180.0:
        diff = 360.0 - diff
    # The scorer treats 0° as "absolute alignment" which is degenerate;
    # the smallest distinguishable angle is ~1° in geocoded data so
    # clamp to that floor.
    return max(diff, 1.0)


def build_topology_index(
    conn: psycopg.Connection[Any],
) -> dict[UUID, SegmentTopology]:
    """Eagerly construct a ``{segment_id: SegmentTopology}`` map.

    One pass over ``road_segments`` collects every segment's
    endpoint keys and lane/class metadata; a second pass groups by
    endpoint key to compute leg counts, min merge angles, and
    neighbor lane/class tuples per junction.
    """
    raw: list[_RawSegmentRow] = []
    with conn.cursor() as cur:
        cur.execute(_LOAD_TOPOLOGY_SQL)
        for row in cur.fetchall():
            seg_id = UUID(str(row[0])) if not isinstance(row[0], UUID) else row[0]
            start_bearing = float(row[5]) if row[5] is not None else 0.0
            end_bearing = float(row[6]) if row[6] is not None else 0.0
            raw.append(
                _RawSegmentRow(
                    segment_id=seg_id,
                    lane_count=_safe_int(row[1], default=2),
                    road_class=str(row[2]),
                    start_key=str(row[3]),
                    end_key=str(row[4]),
                    start_bearing_deg=start_bearing,
                    end_bearing_deg=end_bearing,
                )
            )

    # Group segments by endpoint key. Each entry under a key is a
    # tuple of (segment_id, lane_count, road_class, bearing_into_junction).
    by_key: dict[str, list[tuple[UUID, int, str, float]]] = defaultdict(list)
    for r in raw:
        by_key[r.start_key].append((r.segment_id, r.lane_count, r.road_class, r.start_bearing_deg))
        by_key[r.end_key].append((r.segment_id, r.lane_count, r.road_class, r.end_bearing_deg))

    topology: dict[UUID, SegmentTopology] = {}
    for r in raw:
        start_legs = by_key[r.start_key]
        end_legs = by_key[r.end_key]
        topology[r.segment_id] = SegmentTopology(
            segment_id=r.segment_id,
            lane_count=r.lane_count,
            road_class=r.road_class,
            start_junction=_junction_endpoint(r.segment_id, r.start_bearing_deg, start_legs),
            end_junction=_junction_endpoint(r.segment_id, r.end_bearing_deg, end_legs),
        )
    return topology


def _junction_endpoint(
    own_segment_id: UUID,
    own_bearing_deg: float,
    legs: Sequence[tuple[UUID, int, str, float]],
) -> JunctionEndpoint:
    """Build a JunctionEndpoint from the list of segments sharing one endpoint.

    ``legs`` includes the own segment; the JunctionComplexityScorer
    expects ``neighbor_*`` tuples that exclude the own segment.
    """
    leg_count = len(legs)
    min_angle = 90.0  # default: perpendicular (used when no neighbors)
    neighbor_lanes: list[int] = []
    neighbor_classes: list[str] = []
    for seg_id, lanes, road_class, bearing in legs:
        if seg_id == own_segment_id:
            continue
        neighbor_lanes.append(lanes)
        neighbor_classes.append(road_class)
        angle = _angle_between_bearings(own_bearing_deg, bearing)
        if angle < min_angle:
            min_angle = angle
    return JunctionEndpoint(
        leg_count=leg_count,
        min_merge_angle_deg=min_angle,
        neighbor_lane_counts=tuple(neighbor_lanes),
        neighbor_road_classes=tuple(neighbor_classes),
    )


def make_topology_loader(
    conn: psycopg.Connection[Any],
):  # type: ignore[no-untyped-def]
    """Return a ``TopologyLoader`` callable wired to the connection.

    The returned closure does the load eagerly (one query, full table)
    so per-segment lookups are O(1) hash lookups. A fallback
    SegmentTopology with default values is returned for unknown
    segment ids (the scorer raises rather than producing a bogus
    score otherwise).
    """
    index = build_topology_index(conn)

    def _load(segment_id: UUID) -> SegmentTopology:
        topology = index.get(segment_id)
        if topology is None:
            msg = f"No topology entry for segment {segment_id}"
            raise KeyError(msg)
        return topology

    return _load


# --- Incident loader --------------------------------------------------------

# Per-severity multiplier used by the historical scorer. Matches the
# enum order in scoring/historical/scorer.py:IncidentNearby.severity_weight.
_SEVERITY_WEIGHT: Final[dict[IncidentSeverity, float]] = {
    IncidentSeverity.FATAL: 3.0,
    IncidentSeverity.INJURY: 2.0,
    IncidentSeverity.PROPERTY_DAMAGE_ONLY: 1.0,
    IncidentSeverity.UNKNOWN: 1.0,
}


# All incidents in one query: id, lat, lon (extracted from geom),
# incident_at, severity. Eager load so the per-segment query becomes
# a Python-side haversine filter.
_LOAD_INCIDENTS_SQL: Final[str] = """
SELECT
    id,
    ST_Y(geom) AS lat,
    ST_X(geom) AS lon,
    incident_at,
    severity
FROM incidents
"""


@dataclass(frozen=True, slots=True)
class _IncidentMemo:
    incident_id: UUID
    lat: float
    lon: float
    incident_at: datetime
    severity: IncidentSeverity


def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two WGS84 points."""
    earth_radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return earth_radius_m * c


def make_incident_loader(
    conn: psycopg.Connection[Any],
):  # type: ignore[no-untyped-def]
    """Return an ``IncidentLoader`` callable wired to the connection.

    Pre-loads every row in ``incidents`` into memory; per-segment
    proximity queries become an in-memory linear scan with haversine.
    For Cambridge-scale (3.6k incidents x 36k segments x radius), the
    scan is ~130M ops total — under 2 s in CPython at this size. If
    the dataset grows past 100k incidents, swap this for a per-call
    PostGIS ``ST_DWithin`` query.
    """
    incidents: list[_IncidentMemo] = []
    with conn.cursor() as cur:
        cur.execute(_LOAD_INCIDENTS_SQL)
        for row in cur.fetchall():
            inc_id = UUID(str(row[0])) if not isinstance(row[0], UUID) else row[0]
            try:
                severity = IncidentSeverity(row[4])
            except ValueError:
                severity = IncidentSeverity.UNKNOWN
            incidents.append(
                _IncidentMemo(
                    incident_id=inc_id,
                    lat=float(row[1]),
                    lon=float(row[2]),
                    incident_at=row[3].astimezone(UTC)
                    if row[3].tzinfo
                    else row[3].replace(tzinfo=UTC),
                    severity=severity,
                )
            )

    def _load(segment: ScoringSegment, radius_m: float) -> list[IncidentNearby]:
        results: list[IncidentNearby] = []
        for inc in incidents:
            d = _haversine_meters(segment.lat, segment.lon, inc.lat, inc.lon)
            if d > radius_m:
                continue
            results.append(
                IncidentNearby(
                    incident_id=inc.incident_id,
                    distance_m=d,
                    incident_at=inc.incident_at,
                    severity_weight=_SEVERITY_WEIGHT.get(inc.severity, 1.0),
                )
            )
        return results

    return _load


__all__ = [
    "build_topology_index",
    "make_incident_loader",
    "make_topology_loader",
]
