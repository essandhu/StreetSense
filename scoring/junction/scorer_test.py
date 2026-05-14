"""Unit tests for JunctionComplexityScorer — Phase 4.5.8 + 4.5.10."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from scoring.interface import ScoringSegment, SubScoreResult
from scoring.junction.scorer import (
    JunctionComplexityScorer,
    JunctionEndpoint,
    SegmentTopology,
    compute_score,
)


def _scoring_segment() -> ScoringSegment:
    return ScoringSegment(
        segment_id=uuid4(),
        heading_deg=90.0,
        lat=42.36,
        lon=-71.10,
    )


def _topology(
    *,
    lane_count: int = 2,
    road_class: str = "secondary",
    start_legs: int = 4,
    start_angle: float = 90.0,
    end_legs: int = 4,
    end_angle: float = 90.0,
    start_neighbor_lanes: tuple[int, ...] = (2, 2, 2),
    end_neighbor_lanes: tuple[int, ...] = (2, 2, 2),
    start_neighbor_classes: tuple[str, ...] = ("secondary", "secondary", "secondary"),
    end_neighbor_classes: tuple[str, ...] = ("secondary", "secondary", "secondary"),
) -> SegmentTopology:
    return SegmentTopology(
        segment_id=uuid4(),
        lane_count=lane_count,
        road_class=road_class,
        start_junction=JunctionEndpoint(
            leg_count=start_legs,
            min_merge_angle_deg=start_angle,
            neighbor_lane_counts=start_neighbor_lanes,
            neighbor_road_classes=start_neighbor_classes,
        ),
        end_junction=JunctionEndpoint(
            leg_count=end_legs,
            min_merge_angle_deg=end_angle,
            neighbor_lane_counts=end_neighbor_lanes,
            neighbor_road_classes=end_neighbor_classes,
        ),
    )


def test_scorer_returns_subscore_result_shape() -> None:
    """A 4-leg intersection yields a SubScoreResult in [0, 1] with is_stub=False."""
    topology = _topology()
    scorer = JunctionComplexityScorer(topology_loader=lambda _id: topology)
    segment = _scoring_segment()

    result = scorer.score(segment, at=datetime(2026, 5, 14, tzinfo=UTC))
    assert isinstance(result, SubScoreResult)
    assert 0.0 <= result.value <= 1.0
    assert result.is_stub is False
    assert result.confidence == 1.0


def test_three_leg_is_simpler_than_four_leg() -> None:
    """Monotonicity in intersection degree."""
    three_leg = _topology(
        start_legs=3,
        end_legs=3,
        start_neighbor_lanes=(2, 2),
        end_neighbor_lanes=(2, 2),
        start_neighbor_classes=("secondary", "secondary"),
        end_neighbor_classes=("secondary", "secondary"),
    )
    four_leg = _topology(start_legs=4, end_legs=4)
    assert compute_score(four_leg) > compute_score(three_leg)


def test_acute_merge_is_more_complex_than_perpendicular() -> None:
    """Smaller minimum merge angle -> higher complexity."""
    perpendicular = _topology(start_angle=90.0, end_angle=90.0)
    acute = _topology(start_angle=30.0, end_angle=90.0)
    assert compute_score(acute) > compute_score(perpendicular)


def test_lane_count_change_increases_complexity() -> None:
    """A 4-lane segment merging into 2-lane neighbors scores higher than uniform-2-lane."""
    uniform = _topology(lane_count=2)
    dropping_lanes = _topology(
        lane_count=4,
        start_neighbor_lanes=(2, 2, 2),
        end_neighbor_lanes=(2, 2, 2),
    )
    assert compute_score(dropping_lanes) > compute_score(uniform)


def test_road_class_transition_increases_complexity() -> None:
    """Crossing tier boundaries (e.g., trunk -> residential) scores higher."""
    same_class = _topology(road_class="secondary")
    cross_tier = _topology(
        road_class="trunk",
        start_neighbor_classes=("trunk", "trunk", "trunk"),
        end_neighbor_classes=("residential", "residential", "residential"),
    )
    assert compute_score(cross_tier) > compute_score(same_class)


def test_score_for_samples_is_time_invariant() -> None:
    """All 24 hour-of-day samples return the same SubScoreResult."""
    topology = _topology()
    scorer = JunctionComplexityScorer(topology_loader=lambda _id: topology)
    segment = _scoring_segment()
    ats = [datetime(2026, 5, 14, h, tzinfo=UTC) for h in range(24)]

    results = scorer.score_for_samples(segment, ats)
    assert len(results) == 24
    first = results[0]
    for r in results[1:]:
        assert r == first


def test_dead_end_segment_has_low_complexity() -> None:
    """1-leg endpoints (dead-ends) contribute nothing to intersection degree."""
    dead_end = _topology(
        start_legs=1,
        end_legs=1,
        start_neighbor_lanes=(),
        end_neighbor_lanes=(),
        start_neighbor_classes=(),
        end_neighbor_classes=(),
    )
    score = compute_score(dead_end)
    # No legs -> 0 contribution from degree signal. Angle defaults to
    # 90deg (perpendicular) so that signal is also 0. No lane-count
    # delta + no class transition.
    assert score == pytest.approx(0.0, abs=1e-9)


def test_score_is_clamped_to_unit_interval() -> None:
    """Pathological inputs cannot push the score outside [0, 1]."""
    extreme = _topology(
        start_legs=10,
        end_legs=10,
        start_angle=0.0,
        end_angle=0.0,
        lane_count=8,
        start_neighbor_lanes=(1, 1, 1, 1, 1, 1, 1, 1, 1),
        end_neighbor_lanes=(1, 1, 1, 1, 1, 1, 1, 1, 1),
        road_class="motorway",
        start_neighbor_classes=("service",) * 9,
        end_neighbor_classes=("service",) * 9,
    )
    score = compute_score(extreme)
    assert 0.0 <= score <= 1.0
