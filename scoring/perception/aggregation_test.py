"""Hypothesis property tests for ``scoring.perception.aggregation``."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from scoring.perception.aggregation import PerImageScore, aggregate


@st.composite
def per_image_scores(draw: st.DrawFn, min_size: int = 1, max_size: int = 20) -> list[PerImageScore]:
    values = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
            min_size=min_size,
            max_size=max_size,
        )
    )
    uncerts = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
            min_size=len(values),
            max_size=len(values),
        )
    )
    return [PerImageScore(value=v, uncertainty=u) for v, u in zip(values, uncerts, strict=True)]


@given(per_image_scores())
def test_aggregated_value_in_unit_interval(scores: list[PerImageScore]) -> None:
    """Inputs in [0, 1] → segment value in [0, 1]."""
    result = aggregate(scores)
    assert 0.0 <= result.value <= 1.0
    assert 0.0 <= result.uncertainty <= 1.0


@given(per_image_scores(min_size=2))
def test_order_invariance(scores: list[PerImageScore]) -> None:
    """Permuting input order does not change segment score."""
    a = aggregate(scores)
    b = aggregate(list(reversed(scores)))
    assert a == b


@given(per_image_scores(min_size=1), st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
def test_uncertainty_is_monotonic_in_added_uncertainty(
    scores: list[PerImageScore], extra_unc: float
) -> None:
    """Adding a higher-uncertainty image cannot DECREASE segment uncertainty."""
    base = aggregate(scores).uncertainty
    extra = PerImageScore(value=0.5, uncertainty=max(extra_unc, base))
    augmented = aggregate([*scores, extra]).uncertainty
    assert augmented >= base


def test_empty_input_returns_stub_with_zero_count() -> None:
    """Spec Tech Note 4 stub-fallback: zero imagery → image_count == 0."""
    result = aggregate([])
    assert result.image_count == 0
    assert result.value == 0.0
    assert result.uncertainty == 0.0
