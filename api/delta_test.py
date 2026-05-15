"""Property + unit tests for ``api.delta.compute_segment_delta`` (Task 2.2).

Invariants (per ``conductor/tracks/phase-5-delta-deployment/plan.md`` Task
2.2, with one refinement noted in the track index): the delta function is
**pure** — no I/O, no time, no global state — so it gets ``hypothesis``
property tests covering the algebraic invariants the frontend depends on.

The plan's third invariant ("sub-score deltas sum to composite delta") is
implemented here as the stronger and provable
``composite_delta == local_contribution_delta + propagation_uplift_delta``
— the explainability decomposition (CLAUDE.md §Explainability) carried
into delta-land. Sub-score deltas DO get computed (and asserted to be
field-wise subtractions), they just don't sum to composite_delta without
the weights and the propagation_uplift term.
"""

from __future__ import annotations

import math
from typing import Literal
from uuid import UUID, uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from api.delta import SegmentScoreSnapshot, SubScoreValues, compute_segment_delta
from api.schemas import ConfidenceIndicator

# Hypothesis strategies ----------------------------------------------------

_LimiterName = Literal["freshness", "coverage", "model"]

_unit_floats = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_signed_unit = st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_limiters: st.SearchStrategy[_LimiterName] = st.sampled_from(["freshness", "coverage", "model"])


@st.composite
def _sub_score_values(draw: st.DrawFn) -> SubScoreValues:
    return SubScoreValues(
        lane_marking_quality=draw(_unit_floats),
        glare_exposure=draw(_unit_floats),
        junction_complexity=draw(_unit_floats),
        historical_correlation=draw(_unit_floats),
    )


@st.composite
def _confidence_indicators(draw: st.DrawFn) -> ConfidenceIndicator:
    return ConfidenceIndicator(value=draw(_unit_floats), limiter=draw(_limiters))


@st.composite
def _segment_score_snapshots(
    draw: st.DrawFn, segment_id: UUID | None = None
) -> SegmentScoreSnapshot:
    """Random valid score snapshot.

    composite_risk is constrained to equal local_contribution +
    propagation_uplift so the input itself satisfies Phase 4's
    decomposition; this matches what the database actually stores.
    """
    local = draw(_unit_floats)
    uplift = draw(st.floats(min_value=0.0, max_value=0.5, allow_nan=False, allow_infinity=False))
    return SegmentScoreSnapshot(
        segment_id=segment_id or uuid4(),
        composite_risk=local + uplift,
        local_contribution=local,
        propagation_uplift=uplift,
        sub_scores=draw(_sub_score_values()),
        confidence=draw(_confidence_indicators()),
    )


# Anti-symmetry property ---------------------------------------------------


@given(_segment_score_snapshots(), _segment_score_snapshots())
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_delta_is_anti_symmetric(a: SegmentScoreSnapshot, b: SegmentScoreSnapshot) -> None:
    """compute(a, b) negated == compute(b, a) on every numeric field."""
    # Force same segment_id; the function requires it.
    b = b.model_copy(update={"segment_id": a.segment_id})

    ab = compute_segment_delta(a, b)
    ba = compute_segment_delta(b, a)

    assert math.isclose(ab.composite_delta, -ba.composite_delta, abs_tol=1e-9)
    assert math.isclose(ab.local_contribution_delta, -ba.local_contribution_delta, abs_tol=1e-9)
    assert math.isclose(ab.propagation_uplift_delta, -ba.propagation_uplift_delta, abs_tol=1e-9)
    assert math.isclose(
        ab.sub_score_deltas.lane_marking_quality,
        -ba.sub_score_deltas.lane_marking_quality,
        abs_tol=1e-9,
    )
    assert math.isclose(
        ab.sub_score_deltas.glare_exposure,
        -ba.sub_score_deltas.glare_exposure,
        abs_tol=1e-9,
    )
    assert math.isclose(
        ab.sub_score_deltas.junction_complexity,
        -ba.sub_score_deltas.junction_complexity,
        abs_tol=1e-9,
    )
    assert math.isclose(
        ab.sub_score_deltas.historical_correlation,
        -ba.sub_score_deltas.historical_correlation,
        abs_tol=1e-9,
    )


# Self-zero property -------------------------------------------------------


@given(_segment_score_snapshots())
def test_delta_of_run_with_itself_is_zero(a: SegmentScoreSnapshot) -> None:
    """compute(a, a) produces all-zero deltas on every field."""
    result = compute_segment_delta(a, a)
    assert result.composite_delta == 0.0
    assert result.local_contribution_delta == 0.0
    assert result.propagation_uplift_delta == 0.0
    assert result.sub_score_deltas.lane_marking_quality == 0.0
    assert result.sub_score_deltas.glare_exposure == 0.0
    assert result.sub_score_deltas.junction_complexity == 0.0
    assert result.sub_score_deltas.historical_correlation == 0.0


# Decomposition invariant --------------------------------------------------


@given(_segment_score_snapshots(), _segment_score_snapshots())
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_composite_delta_decomposes_into_local_plus_uplift(
    a: SegmentScoreSnapshot, b: SegmentScoreSnapshot
) -> None:
    """composite_delta == local_contribution_delta + propagation_uplift_delta.

    This is the explainability invariant from CLAUDE.md carried into the
    delta path. It holds because the *inputs* satisfy
    ``composite_risk == local_contribution + propagation_uplift`` (the
    Phase-4 stored shape), so delta-of-sums equals sum-of-deltas.
    """
    b = b.model_copy(update={"segment_id": a.segment_id})
    result = compute_segment_delta(a, b)
    assert math.isclose(
        result.composite_delta,
        result.local_contribution_delta + result.propagation_uplift_delta,
        abs_tol=1e-9,
    )


# Convention test ----------------------------------------------------------


def test_positive_composite_delta_means_b_higher_than_a() -> None:
    """Positive composite_delta means run_b had higher risk than run_a.

    This is the documented convention so the frontend can color "risk
    went up" consistently.
    """
    sid = uuid4()
    a = SegmentScoreSnapshot(
        segment_id=sid,
        composite_risk=0.3,
        local_contribution=0.3,
        propagation_uplift=0.0,
        sub_scores=SubScoreValues(
            lane_marking_quality=0.3,
            glare_exposure=0.3,
            junction_complexity=0.3,
            historical_correlation=0.3,
        ),
        confidence=ConfidenceIndicator(value=0.8, limiter="coverage"),
    )
    b = a.model_copy(update={"composite_risk": 0.5, "local_contribution": 0.5})

    result = compute_segment_delta(a, b)
    assert result.composite_delta == pytest.approx(0.2)


# Field-wise sub-score deltas ---------------------------------------------


def test_sub_score_deltas_are_field_wise_subtraction() -> None:
    """Each sub_score_delta is exactly ``b.sub_score - a.sub_score``."""
    sid = uuid4()
    a = SegmentScoreSnapshot(
        segment_id=sid,
        composite_risk=0.4,
        local_contribution=0.4,
        propagation_uplift=0.0,
        sub_scores=SubScoreValues(
            lane_marking_quality=0.10,
            glare_exposure=0.20,
            junction_complexity=0.30,
            historical_correlation=0.40,
        ),
        confidence=ConfidenceIndicator(value=0.8, limiter="coverage"),
    )
    b = a.model_copy(
        update={
            "sub_scores": SubScoreValues(
                lane_marking_quality=0.15,
                glare_exposure=0.18,
                junction_complexity=0.30,
                historical_correlation=0.50,
            )
        }
    )
    result = compute_segment_delta(a, b)
    assert result.sub_score_deltas.lane_marking_quality == pytest.approx(0.05)
    assert result.sub_score_deltas.glare_exposure == pytest.approx(-0.02)
    assert result.sub_score_deltas.junction_complexity == pytest.approx(0.0)
    assert result.sub_score_deltas.historical_correlation == pytest.approx(0.10)


# Confidence pass-through --------------------------------------------------


def test_delta_carries_both_confidence_indicators_unchanged() -> None:
    """The delta row carries confidence_a and confidence_b verbatim — the
    delta function does NOT take a min or combine them. The UI labels
    each end of the comparison separately."""
    sid = uuid4()
    conf_a = ConfidenceIndicator(value=0.7, limiter="freshness")
    conf_b = ConfidenceIndicator(value=0.9, limiter="model")
    a = SegmentScoreSnapshot(
        segment_id=sid,
        composite_risk=0.4,
        local_contribution=0.4,
        propagation_uplift=0.0,
        sub_scores=SubScoreValues(
            lane_marking_quality=0.1,
            glare_exposure=0.1,
            junction_complexity=0.1,
            historical_correlation=0.1,
        ),
        confidence=conf_a,
    )
    b = a.model_copy(update={"confidence": conf_b})

    result = compute_segment_delta(a, b)
    assert result.confidence_a == conf_a
    assert result.confidence_b == conf_b


# Mismatched segment_id is a programmer error ----------------------------


def test_mismatched_segment_ids_raise_value_error() -> None:
    """The repo (Task 2.3) JOINs on segment_id, so by the time scores
    reach this function they share a segment_id. A mismatch is a
    programmer error and surfaces loudly."""
    a = SegmentScoreSnapshot(
        segment_id=uuid4(),
        composite_risk=0.4,
        local_contribution=0.4,
        propagation_uplift=0.0,
        sub_scores=SubScoreValues(
            lane_marking_quality=0.1,
            glare_exposure=0.1,
            junction_complexity=0.1,
            historical_correlation=0.1,
        ),
        confidence=ConfidenceIndicator(value=0.8, limiter="coverage"),
    )
    b = a.model_copy(update={"segment_id": uuid4()})

    with pytest.raises(ValueError, match="segment_id"):
        compute_segment_delta(a, b)


# Output segment_id is the shared one --------------------------------------


def test_output_segment_id_matches_inputs() -> None:
    sid = uuid4()
    a = SegmentScoreSnapshot(
        segment_id=sid,
        composite_risk=0.4,
        local_contribution=0.4,
        propagation_uplift=0.0,
        sub_scores=SubScoreValues(
            lane_marking_quality=0.1,
            glare_exposure=0.1,
            junction_complexity=0.1,
            historical_correlation=0.1,
        ),
        confidence=ConfidenceIndicator(value=0.8, limiter="coverage"),
    )
    b = a.model_copy()
    result = compute_segment_delta(a, b)
    assert result.segment_id == sid
