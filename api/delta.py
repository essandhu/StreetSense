"""Pure-functional per-segment delta computation (Phase 5 Task 2.2).

Computes the field-wise difference between two per-segment score
snapshots (one from each scoring run, paired on ``segment_id`` and
``t`` by the repository layer in Task 2.3). The output is a
:class:`api.schemas.SegmentDelta` ready to ship at the API boundary.

This module is **pure**: no I/O, no ``datetime.now()``, no global state.
Determinism is enforced by the property tests in ``api/delta_test.py``.

Decomposition invariant (CLAUDE.md §Explainability, carried into the
delta path):

    composite_delta == local_contribution_delta + propagation_uplift_delta

Holds because the *inputs* satisfy
``composite_risk == local_contribution + propagation_uplift`` (Phase 4's
stored shape; see ``api/schemas.py`` ``SegmentDetail``), so the delta of
the sum equals the sum of the deltas.

Plan deviation note: the plan named ``api/services/delta.py`` for this
module. The existing codebase puts pure-functional, API-adjacent compute
in flat files (``api/confidence.py``); this module follows that
convention. See ``conductor/tracks/phase-5-delta-deployment/index.md``
under "Discovered during implementation".
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.schemas import ConfidenceIndicator, SegmentDelta, SubScoreDeltas


class SubScoreValues(BaseModel):
    """The four sub-score values for one (segment, t).

    Decoupled from :class:`api.schemas.SubScore` (which carries
    ``value``, ``confidence``, ``is_stub``, ``metadata``) because the
    delta computation only needs the values. The repository layer
    (Task 2.3) populates this from the segment_scores row's sub-score
    columns; ``is_stub`` / ``metadata`` ride alongside in the row but
    don't enter the delta.
    """

    model_config = ConfigDict(frozen=True)

    lane_marking_quality: float = Field(..., ge=0.0, le=1.0)
    glare_exposure: float = Field(..., ge=0.0, le=1.0)
    junction_complexity: float = Field(..., ge=0.0, le=1.0)
    historical_correlation: float = Field(..., ge=0.0, le=1.0)


class SegmentScoreSnapshot(BaseModel):
    """One ``segment_scores`` row as the delta service consumes it.

    Frozen, immutable, ready to be passed between the repository and the
    pure compute. The repo constructs it from a single row of
    ``SELECT segment_id, composite_risk, local_contribution,
    propagation_uplift, sub_score_lane_marking_quality, ...,
    confidence_value, confidence_limiter FROM segment_scores ...``.
    """

    model_config = ConfigDict(frozen=True)

    segment_id: UUID
    composite_risk: float = Field(..., ge=0.0)
    local_contribution: float = Field(..., ge=0.0)
    propagation_uplift: float = Field(..., ge=0.0)
    sub_scores: SubScoreValues
    confidence: ConfidenceIndicator


def compute_segment_delta(
    score_a: SegmentScoreSnapshot, score_b: SegmentScoreSnapshot
) -> SegmentDelta:
    """Compute ``score_b - score_a`` field-wise.

    Convention: positive ``composite_delta`` means risk went up from
    ``run_a`` to ``run_b``.

    Raises:
        ValueError: if the two snapshots reference different segments.
            The repository layer (Task 2.3) JOINs on segment_id so a
            mismatch here is a programmer error worth surfacing loudly.
    """
    if score_a.segment_id != score_b.segment_id:
        raise ValueError(
            f"segment_id mismatch: {score_a.segment_id!s} vs {score_b.segment_id!s}; "
            "compute_segment_delta requires both snapshots to reference the same segment."
        )

    return SegmentDelta(
        segment_id=score_a.segment_id,
        composite_delta=score_b.composite_risk - score_a.composite_risk,
        local_contribution_delta=score_b.local_contribution - score_a.local_contribution,
        propagation_uplift_delta=score_b.propagation_uplift - score_a.propagation_uplift,
        sub_score_deltas=SubScoreDeltas(
            lane_marking_quality=(
                score_b.sub_scores.lane_marking_quality - score_a.sub_scores.lane_marking_quality
            ),
            glare_exposure=(score_b.sub_scores.glare_exposure - score_a.sub_scores.glare_exposure),
            junction_complexity=(
                score_b.sub_scores.junction_complexity - score_a.sub_scores.junction_complexity
            ),
            historical_correlation=(
                score_b.sub_scores.historical_correlation
                - score_a.sub_scores.historical_correlation
            ),
        ),
        confidence_a=score_a.confidence,
        confidence_b=score_b.confidence,
    )


__all__ = ["SegmentScoreSnapshot", "SubScoreValues", "compute_segment_delta"]
