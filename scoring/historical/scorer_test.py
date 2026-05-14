"""Unit tests for HistoricalCorrelationScorer — Phase 4.5.11 + 4.5.13."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from scoring.historical.scorer import (
    HistoricalCorrelationScorer,
    IncidentNearby,
    compute_raw_weight,
)
from scoring.interface import ScoringSegment, SubScoreResult


def _scoring_segment() -> ScoringSegment:
    return ScoringSegment(
        segment_id=uuid4(),
        heading_deg=90.0,
        lat=42.36,
        lon=-71.10,
    )


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


# ---------------------------------------------------------------------------
# compute_raw_weight pure-function tests
# ---------------------------------------------------------------------------


def test_zero_distance_zero_age_max_weight() -> None:
    """Distance 0 + age 0 -> spatial exp(0) * recency exp(0) = 1.0."""
    run_at = _utc(2026, 5, 14)
    weight = compute_raw_weight(distance_m=0.0, incident_at=run_at, run_at=run_at)
    assert weight == pytest.approx(1.0, abs=1e-9)


def test_distance_beyond_radius_is_near_zero() -> None:
    """Spatial Gaussian falls off rapidly past the radius (sigma = radius/2)."""
    run_at = _utc(2026, 5, 14)
    far = compute_raw_weight(distance_m=200.0, incident_at=run_at, run_at=run_at, radius_m=50.0)
    near = compute_raw_weight(distance_m=10.0, incident_at=run_at, run_at=run_at, radius_m=50.0)
    assert far < near
    assert far < 1e-6  # 4 sigma -> negligible


def test_recency_decay_halves_at_half_life() -> None:
    """Exponential recency: an incident half_life days old weighs half a fresh one."""
    run_at = _utc(2026, 5, 14)
    fresh = compute_raw_weight(
        distance_m=0.0, incident_at=run_at, run_at=run_at, half_life_days=365.0
    )
    old = compute_raw_weight(
        distance_m=0.0,
        incident_at=run_at - timedelta(days=365),
        run_at=run_at,
        half_life_days=365.0,
    )
    assert old == pytest.approx(fresh * 0.5, abs=1e-3)


def test_severity_weight_is_multiplicative() -> None:
    run_at = _utc(2026, 5, 14)
    fatal = compute_raw_weight(
        distance_m=0.0, incident_at=run_at, run_at=run_at, severity_weight=3.0
    )
    pdo = compute_raw_weight(distance_m=0.0, incident_at=run_at, run_at=run_at, severity_weight=1.0)
    assert fatal == pytest.approx(pdo * 3.0, abs=1e-9)


# ---------------------------------------------------------------------------
# HistoricalCorrelationScorer integration tests
# ---------------------------------------------------------------------------


def test_scorer_returns_subscore_result_shape() -> None:
    """A segment near one incident yields a SubScoreResult with value > 0."""
    incidents = [
        IncidentNearby(
            incident_id=uuid4(),
            distance_m=20.0,
            incident_at=_utc(2026, 5, 1),
        )
    ]
    scorer = HistoricalCorrelationScorer(
        incident_loader=lambda _seg, _r: incidents,
        run_at=_utc(2026, 5, 14),
    )
    result = scorer.score(_scoring_segment(), at=_utc(2026, 5, 14))
    assert isinstance(result, SubScoreResult)
    assert 0.0 < result.value <= 1.0
    assert result.is_stub is False


def test_segment_far_from_incidents_has_low_score() -> None:
    """All incidents beyond ~3 sigma -> total weight near 0."""
    incidents = [
        IncidentNearby(
            incident_id=uuid4(),
            distance_m=500.0,
            incident_at=_utc(2026, 5, 1),
        )
    ]
    scorer = HistoricalCorrelationScorer(
        incident_loader=lambda _seg, _r: incidents,
        run_at=_utc(2026, 5, 14),
    )
    result = scorer.score(_scoring_segment(), at=_utc(2026, 5, 14))
    assert result.value < 0.01


def test_no_incidents_yields_zero() -> None:
    scorer = HistoricalCorrelationScorer(
        incident_loader=lambda _seg, _r: [],
        run_at=_utc(2026, 5, 14),
    )
    result = scorer.score(_scoring_segment(), at=_utc(2026, 5, 14))
    assert result.value == pytest.approx(0.0, abs=1e-12)


def test_kde_radius_is_configurable() -> None:
    """A wider radius captures more weight from distant incidents."""
    incidents = [
        IncidentNearby(
            incident_id=uuid4(),
            distance_m=60.0,
            incident_at=_utc(2026, 5, 14),
        )
    ]
    narrow = HistoricalCorrelationScorer(
        incident_loader=lambda _seg, _r: incidents,
        run_at=_utc(2026, 5, 14),
        radius_m=50.0,
    )
    wide = HistoricalCorrelationScorer(
        incident_loader=lambda _seg, _r: incidents,
        run_at=_utc(2026, 5, 14),
        radius_m=200.0,
    )
    assert wide.raw_weight_sum(_scoring_segment()) > narrow.raw_weight_sum(_scoring_segment())


def test_recency_weighting_favors_recent_incidents() -> None:
    """A recent + a year-old incident at same distance: recent contributes ~2x."""
    incidents = [
        IncidentNearby(
            incident_id=uuid4(),
            distance_m=10.0,
            incident_at=_utc(2026, 5, 14),
        ),
        IncidentNearby(
            incident_id=uuid4(),
            distance_m=10.0,
            incident_at=_utc(2025, 5, 14),
        ),
    ]
    scorer = HistoricalCorrelationScorer(
        incident_loader=lambda _seg, _r: incidents,
        run_at=_utc(2026, 5, 14),
        half_life_days=365.0,
    )
    total = scorer.raw_weight_sum(_scoring_segment())
    # Fresh contributes ~1; year-old contributes ~0.5; ratio ~1.5x.
    assert 1.0 < total < 2.0


def test_score_for_samples_is_time_invariant() -> None:
    """All 24 hour-of-day samples return the same SubScoreResult."""
    incidents = [
        IncidentNearby(
            incident_id=uuid4(),
            distance_m=15.0,
            incident_at=_utc(2026, 5, 14),
        )
    ]
    scorer = HistoricalCorrelationScorer(
        incident_loader=lambda _seg, _r: incidents,
        run_at=_utc(2026, 5, 14),
    )
    segment = _scoring_segment()
    ats = [datetime(2026, 5, 14, h, tzinfo=UTC) for h in range(24)]

    results = scorer.score_for_samples(segment, ats)
    assert len(results) == 24
    first = results[0]
    for r in results[1:]:
        assert r == first


def test_normalization_clips_to_unit_interval() -> None:
    """With max_weight set, score = min(1, raw/max)."""
    incidents = [
        IncidentNearby(
            incident_id=uuid4(),
            distance_m=0.0,
            incident_at=_utc(2026, 5, 14),
        )
    ]
    scorer = HistoricalCorrelationScorer(
        incident_loader=lambda _seg, _r: incidents,
        run_at=_utc(2026, 5, 14),
        max_weight=2.0,
    )
    result = scorer.score(_scoring_segment(), at=_utc(2026, 5, 14))
    # raw = 1.0; max = 2.0; value = 0.5.
    assert result.value == pytest.approx(0.5, abs=1e-9)
    assert result.metadata["normalized"] is True


def test_naive_datetime_raises() -> None:
    """run_at without tzinfo is a programmer error -- raises immediately."""
    with pytest.raises(ValueError, match="timezone-aware"):
        HistoricalCorrelationScorer(
            incident_loader=lambda _seg, _r: [],
            run_at=datetime(2026, 5, 14),  # no tzinfo
        )
