"""Junction-complexity scorer — Phase 4.5.10.

Implements the :class:`SubScorer` protocol from ``scoring.interface``.
Per-segment complexity score in [0, 1] computed from OSM topology
alone -- no imagery, no external data.

The score combines four signals, weighted-summed and clipped to [0, 1]:

1. **Intersection degree** at the segment's endpoints (number of legs
   meeting). 4-way > 3-way; T-intersection > end-of-road.
2. **Merge-angle sharpness** (minimum angle between this segment and
   any other edge at the same junction). Sharper merges score higher.
3. **Lane-count change** between this segment and its endpoint
   neighbors. Lane drops + lane adds both add complexity.
4. **Road-class transition** at endpoints (e.g., motorway -> trunk).
   Cross-class endpoints score higher than same-class.

Pure-functional and deterministic. Time-invariant at the per-image
scale: ``score_for_samples(segment, ats)`` returns the same value for
all 24 hour-of-day samples (topology does not change hourly).

The scorer is decoupled from the data layer through a ``topology_loader``
callable: callers decide whether segment topology comes from the
``road_segments`` + ``road_junctions`` PostGIS tables (production),
pre-loaded fixtures (unit tests), or anywhere else.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from scoring.interface import ScoringSegment, SubScoreResult


@dataclass(frozen=True, slots=True)
class JunctionEndpoint:
    """Topology at one endpoint of a road segment.

    A segment has two endpoints (start + end). Each endpoint sits at
    a junction whose complexity depends on the leg count + merge
    angles + the relative road classes of meeting segments.
    """

    leg_count: int
    """Number of edges (including this segment) meeting at this
    junction. >= 1; 1 means dead-end."""

    min_merge_angle_deg: float
    """Minimum angle in degrees between this segment and any other
    edge at the same junction. Range (0, 180]. 90 is perpendicular;
    < 30 is a sharp merge."""

    neighbor_lane_counts: tuple[int, ...] = ()
    """Lane counts of segments meeting at this junction, excluding
    this segment. Empty if leg_count == 1."""

    neighbor_road_classes: tuple[str, ...] = ()
    """OSM `highway` tag values of segments at this junction,
    excluding this segment. Empty if leg_count == 1."""


@dataclass(frozen=True, slots=True)
class SegmentTopology:
    """Topology summary required to score a segment's junction complexity.

    The fields are the minimum surface the scorer needs; the full OSM
    `RoadSegment` record carries more.
    """

    segment_id: UUID
    lane_count: int
    road_class: str
    start_junction: JunctionEndpoint
    end_junction: JunctionEndpoint


# Loader contract: given a segment UUID, return its topology. Callers
# decide the data source; the scorer stays pure-functional given the
# loader's output.
TopologyLoader = Callable[[UUID], SegmentTopology]


# Weight defaults documented in ADR 0006's parameter discussion.
_WEIGHT_INTERSECTION_DEGREE = 0.30
_WEIGHT_MERGE_ANGLE = 0.30
_WEIGHT_LANE_COUNT_CHANGE = 0.20
_WEIGHT_ROAD_CLASS_TRANSITION = 0.20

# Road-class hierarchy (rough OSM highway tag ordering, low-to-high
# importance). Used by the road-class-transition signal to weight
# cross-class transitions more than intra-class ones.
_ROAD_CLASS_RANK: dict[str, int] = {
    "service": 1,
    "residential": 2,
    "living_street": 2,
    "unclassified": 2,
    "tertiary": 3,
    "tertiary_link": 3,
    "secondary": 4,
    "secondary_link": 4,
    "primary": 5,
    "primary_link": 5,
    "trunk": 6,
    "trunk_link": 6,
    "motorway": 7,
    "motorway_link": 7,
}


def _intersection_degree_score(topology: SegmentTopology) -> float:
    """Higher leg counts -> higher complexity; capped at 5+ ways."""
    max_legs = max(
        topology.start_junction.leg_count,
        topology.end_junction.leg_count,
    )
    if max_legs <= 1:
        return 0.0
    # 2-leg endpoints (mid-segment join) are simple; 3-way is medium;
    # 4-way is high; 5+ saturates.
    return min(1.0, (max_legs - 2) / 3.0)


def _merge_angle_score(topology: SegmentTopology) -> float:
    """Smaller minimum merge angles -> higher complexity."""
    min_angle = min(
        topology.start_junction.min_merge_angle_deg,
        topology.end_junction.min_merge_angle_deg,
    )
    # Inverse linear from 90deg (perpendicular, easy) to 0deg (sharp,
    # hard). Clamped at [0, 1].
    return max(0.0, min(1.0, (90.0 - min_angle) / 90.0))


def _lane_count_change_score(topology: SegmentTopology) -> float:
    """Larger deltas vs neighbors -> higher complexity."""
    deltas: list[float] = []
    for endpoint in (topology.start_junction, topology.end_junction):
        for neighbor_lanes in endpoint.neighbor_lane_counts:
            deltas.append(abs(topology.lane_count - neighbor_lanes))
    if not deltas:
        return 0.0
    # A 1-lane delta scores 0.5; a 3-lane delta saturates.
    return min(1.0, max(deltas) / 3.0)


def _road_class_transition_score(topology: SegmentTopology) -> float:
    """Cross-tier transitions -> higher complexity."""
    own_rank = _ROAD_CLASS_RANK.get(topology.road_class, 2)
    max_rank_delta = 0
    for endpoint in (topology.start_junction, topology.end_junction):
        for neighbor_class in endpoint.neighbor_road_classes:
            neighbor_rank = _ROAD_CLASS_RANK.get(neighbor_class, 2)
            max_rank_delta = max(max_rank_delta, abs(own_rank - neighbor_rank))
    return min(1.0, max_rank_delta / 3.0)


def compute_score(topology: SegmentTopology) -> float:
    """Combined junction-complexity score in [0, 1].

    Pure function of topology; deterministic; no I/O.
    """
    score = (
        _WEIGHT_INTERSECTION_DEGREE * _intersection_degree_score(topology)
        + _WEIGHT_MERGE_ANGLE * _merge_angle_score(topology)
        + _WEIGHT_LANE_COUNT_CHANGE * _lane_count_change_score(topology)
        + _WEIGHT_ROAD_CLASS_TRANSITION * _road_class_transition_score(topology)
    )
    return max(0.0, min(1.0, score))


class JunctionComplexityScorer:
    """SubScorer producing per-segment junction-complexity scores.

    Constructed with a ``topology_loader`` that resolves segment_id
    to ``SegmentTopology``. The scorer's ``score()`` method is then
    pure-functional given that loader's output.

    The ``score_for_samples()`` batched variant is time-invariant:
    topology does not change hourly, so the same value is returned
    for every timestamp. Implemented for shape-consistency with the
    SubScorer registry and the scoring run's batched API.
    """

    name: ClassVar[str] = "junction_complexity"

    def __init__(self, topology_loader: TopologyLoader) -> None:
        self._topology_loader = topology_loader

    def score(self, segment: ScoringSegment, *, at: datetime) -> SubScoreResult:
        """Compute the per-segment junction-complexity score at time ``at``.

        Time-invariant: ``at`` is unused. The parameter is kept to
        satisfy the SubScorer Protocol so the scoring run's
        orchestration doesn't need to special-case time-invariant
        scorers.
        """
        del at  # documented unused
        topology = self._topology_loader(segment.segment_id)
        value = compute_score(topology)
        return SubScoreResult(
            value=value,
            confidence=1.0,  # OSM topology is high-confidence input data
            is_stub=False,
            metadata={
                "max_leg_count": max(
                    topology.start_junction.leg_count,
                    topology.end_junction.leg_count,
                ),
                "min_merge_angle_deg": min(
                    topology.start_junction.min_merge_angle_deg,
                    topology.end_junction.min_merge_angle_deg,
                ),
                "road_class": topology.road_class,
                "lane_count": topology.lane_count,
            },
        )

    def score_for_samples(
        self,
        segment: ScoringSegment,
        ats: Sequence[datetime],
    ) -> list[SubScoreResult]:
        """Batched variant returning the same value for every timestamp.

        Junction complexity is a function of OSM topology, which is
        static at the per-image scale. Subsequent timestamps reuse
        the same SubScoreResult so the scoring run's per-hour fan-out
        carries the cost only once per segment.
        """
        if not ats:
            return []
        result = self.score(segment, at=ats[0])
        return [result] * len(ats)


__all__ = [
    "JunctionComplexityScorer",
    "JunctionEndpoint",
    "SegmentTopology",
    "TopologyLoader",
    "compute_score",
]
