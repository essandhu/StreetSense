"""Unit + property tests for scoring/composite.py — Phase 4.6.9."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from scoring.composite import (
    DEFAULT_COMPOSITE_WEIGHTS,
    CompositeBreakdown,
    assemble,
    local_aggregate,
)

# ---------------------------------------------------------------------------
# Unit tests — pinning literal numbers
# ---------------------------------------------------------------------------


def test_default_weights_sum_to_one() -> None:
    """The default-weighting interprets local_aggregate as a weighted average."""
    total = sum(DEFAULT_COMPOSITE_WEIGHTS.values())
    assert math.isclose(total, 1.0, abs_tol=1e-9)


def test_local_aggregate_with_zero_inputs_is_zero() -> None:
    zero_inputs = {name: 0.0 for name in DEFAULT_COMPOSITE_WEIGHTS}
    assert local_aggregate(zero_inputs) == 0.0


def test_local_aggregate_with_unit_inputs_equals_weight_sum() -> None:
    """If every sub-score is 1.0, local_aggregate equals sum of weights."""
    unit_inputs = {name: 1.0 for name in DEFAULT_COMPOSITE_WEIGHTS}
    expected = sum(DEFAULT_COMPOSITE_WEIGHTS.values())
    assert math.isclose(local_aggregate(unit_inputs), expected, abs_tol=1e-9)


def test_local_aggregate_uses_default_weights() -> None:
    """Concrete arithmetic on a fixed input set matches hand-computed result."""
    sub_scores = {
        "glare": 0.8,
        "lane_marking": 0.5,
        "junction_complexity": 0.6,
        "historical": 0.4,
    }
    # 0.35*0.8 + 0.30*0.5 + 0.20*0.6 + 0.15*0.4 = 0.28 + 0.15 + 0.12 + 0.06 = 0.61
    assert math.isclose(local_aggregate(sub_scores), 0.61, abs_tol=1e-9)


def test_local_aggregate_override_weights() -> None:
    """Callers can override weights without touching defaults."""
    sub_scores = {"a": 1.0, "b": 0.0}
    weights = {"a": 0.7, "b": 0.3}
    assert local_aggregate(sub_scores, weights) == pytest.approx(0.7, abs=1e-9)


def test_local_aggregate_missing_key_raises() -> None:
    """A missing required sub-score is a programmer error -- raise immediately."""
    with pytest.raises(KeyError):
        local_aggregate({"glare": 0.5})  # missing other keys


def test_assemble_decomposes_composite_into_named_components() -> None:
    sub_scores = {
        "glare": 0.8,
        "lane_marking": 0.5,
        "junction_complexity": 0.6,
        "historical": 0.4,
    }
    breakdown = assemble(sub_scores, propagation_uplift=0.25)

    assert isinstance(breakdown, CompositeBreakdown)
    assert breakdown.local_contribution == pytest.approx(0.61, abs=1e-9)
    assert breakdown.propagation_uplift == pytest.approx(0.25, abs=1e-9)
    assert breakdown.composite_risk == pytest.approx(0.86, abs=1e-9)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


_FLOAT_STRATEGY = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@given(
    glare=_FLOAT_STRATEGY,
    lane=_FLOAT_STRATEGY,
    junction=_FLOAT_STRATEGY,
    historical=_FLOAT_STRATEGY,
    uplift=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200, deadline=5000)
def test_composite_equals_local_plus_uplift(
    glare: float, lane: float, junction: float, historical: float, uplift: float
) -> None:
    """composite_risk = local_contribution + propagation_uplift, exactly."""
    sub_scores = {
        "glare": glare,
        "lane_marking": lane,
        "junction_complexity": junction,
        "historical": historical,
    }
    breakdown = assemble(sub_scores, uplift)
    assert math.isclose(
        breakdown.composite_risk,
        breakdown.local_contribution + breakdown.propagation_uplift,
        abs_tol=1e-12,
    )


@given(
    glare=_FLOAT_STRATEGY,
    lane=_FLOAT_STRATEGY,
    junction=_FLOAT_STRATEGY,
    historical=_FLOAT_STRATEGY,
)
@settings(max_examples=100, deadline=5000)
def test_local_aggregate_is_bounded(
    glare: float, lane: float, junction: float, historical: float
) -> None:
    """With weights summing to 1 and inputs in [0, 1], local_aggregate is in [0, 1]."""
    sub_scores = {
        "glare": glare,
        "lane_marking": lane,
        "junction_complexity": junction,
        "historical": historical,
    }
    value = local_aggregate(sub_scores)
    assert 0.0 <= value <= 1.0


@given(
    sub_scores_dict=st.fixed_dictionaries(
        {
            "glare": _FLOAT_STRATEGY,
            "lane_marking": _FLOAT_STRATEGY,
            "junction_complexity": _FLOAT_STRATEGY,
            "historical": _FLOAT_STRATEGY,
        }
    )
)
@settings(max_examples=50, deadline=5000)
def test_local_aggregate_is_permutation_invariant_for_same_weights(
    sub_scores_dict: dict[str, float],
) -> None:
    """Iterating weights in different orders does not change local_aggregate."""
    weights_orig = dict(DEFAULT_COMPOSITE_WEIGHTS)
    weights_reordered = dict(reversed(list(DEFAULT_COMPOSITE_WEIGHTS.items())))
    assert math.isclose(
        local_aggregate(sub_scores_dict, weights_orig),
        local_aggregate(sub_scores_dict, weights_reordered),
        abs_tol=1e-12,
    )
