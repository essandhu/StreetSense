"""Property tests for the confidence assembly (spec Tech Note 4)."""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import given
from hypothesis import strategies as st

from api.confidence import assemble, coverage, freshness

_unit_floats = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@given(_unit_floats, _unit_floats, _unit_floats)
def test_confidence_in_unit_interval(f: float, c: float, u: float) -> None:
    """For any inputs in [0, 1], the assembled value lies in [0, 1]."""
    result = assemble(f, c, u)
    assert 0.0 <= result.value <= 1.0


@given(_unit_floats, _unit_floats, _unit_floats)
def test_confidence_equals_min_of_three(f: float, c: float, u: float) -> None:
    """confidence == min(freshness, coverage, 1 - model_uncertainty)."""
    expected = min(f, c, 1.0 - u)
    result = assemble(f, c, u)
    assert result.value == expected


def test_limiter_tie_break_priority() -> None:
    """When values tie, freshness wins, then coverage, then model."""
    # All three equal -> freshness.
    assert assemble(0.5, 0.5, 0.5).limiter == "freshness"
    # Coverage vs model equal, both lower than freshness -> coverage.
    assert assemble(1.0, 0.3, 0.7).limiter == "coverage"
    # Freshness vs model equal, both lower than coverage -> freshness.
    assert assemble(0.4, 1.0, 0.6).limiter == "freshness"
    # model wins outright when 1 - u is strictly smallest.
    assert assemble(1.0, 1.0, 0.9).limiter == "model"


@given(st.integers(min_value=0, max_value=1500))
def test_freshness_monotonic_in_age(age_days: int) -> None:
    """As age increases, freshness must monotonically decrease (or stay the same)."""
    now = date(2026, 5, 14)
    cur = freshness(now - timedelta(days=age_days), now=now)
    nxt = freshness(now - timedelta(days=age_days + 1), now=now)
    assert nxt <= cur


def test_freshness_full_credit_when_fresh() -> None:
    now = date(2026, 5, 14)
    # Within the full-credit window (default 180 days).
    assert freshness(now - timedelta(days=30), now=now) == 1.0
    assert freshness(now - timedelta(days=180), now=now) == 1.0


def test_freshness_zero_when_aged_out() -> None:
    now = date(2026, 5, 14)
    # Past the zero-decay point (default 1080 days).
    assert freshness(now - timedelta(days=1080), now=now) == 0.0
    assert freshness(now - timedelta(days=2000), now=now) == 0.0


@given(st.integers(min_value=0, max_value=10_000), st.integers(min_value=1, max_value=10))
def test_coverage_clamps_to_one_when_actual_meets_or_exceeds_target(
    actual: int, target: int
) -> None:
    if actual >= target:
        assert coverage(actual, target) == 1.0
    else:
        assert 0.0 <= coverage(actual, target) <= 1.0


def test_coverage_zero_when_no_samples() -> None:
    assert coverage(0, 5) == 0.0
