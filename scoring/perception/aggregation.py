"""Pure aggregation: per-image scores → per-segment ``SubScoreResult``.

Spec Tech Note 2: the perception scorer scores each sampled image,
then aggregates:

- ``lane_marking_quality`` = image-weighted mean of per-image scores.
  Phase 3 weights every image equally (every fetch costs the same;
  no priors).
- ``model_uncertainty`` = max over per-image uncertainties — a
  worst-case posture so one bad image limits the segment's overall
  trust. This pairs naturally with the min-rule confidence indicator
  (Tech Note 4 / Phase 3.5).

This module is intentionally I/O-free so it can be property-tested
with Hypothesis (Task 3.3.4) without any test scaffolding.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PerImageScore:
    """One image's contribution to the segment-level score."""

    value: float
    """``lane_marking_quality`` ∈ [0, 1] for this image."""

    uncertainty: float
    """Model uncertainty for this image, ∈ [0, 1]. Higher = less trustworthy."""


@dataclass(frozen=True, slots=True)
class AggregatedScore:
    """Result of aggregating ``PerImageScore``s across a segment."""

    value: float
    """Segment-level ``lane_marking_quality``."""

    uncertainty: float
    """Segment-level ``model_uncertainty``: the per-image max."""

    image_count: int
    """How many per-image scores fed the aggregation. Zero implies stub fallback."""


def aggregate(scores: list[PerImageScore]) -> AggregatedScore:
    """Combine per-image scores into a segment-level result.

    Empty input returns ``AggregatedScore(0.0, 0.0, 0)`` — callers see
    ``image_count == 0`` and decide whether to write a stub row
    (Tech Note 4 / spec stub-fallback rule).

    Invariants (asserted by ``aggregation_test.py``):

    - If every ``value ∈ [0, 1]``, the aggregated ``value ∈ [0, 1]``.
    - The aggregation is order-invariant: ``aggregate(s)`` == ``aggregate(reversed(s))``.
    - ``uncertainty`` is monotonic-non-decreasing in any per-image
      uncertainty (adding a more-uncertain image cannot *decrease*
      the segment uncertainty).
    """
    if not scores:
        return AggregatedScore(value=0.0, uncertainty=0.0, image_count=0)
    n = len(scores)
    value = sum(s.value for s in scores) / n
    uncertainty = max(s.uncertainty for s in scores)
    return AggregatedScore(value=value, uncertainty=uncertainty, image_count=n)


__all__ = ["AggregatedScore", "PerImageScore", "aggregate"]
