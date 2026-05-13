"""Unit tests for `ScoringRunConfig` validation — Task 2.3.7.

Defends the reproducibility invariant at the orchestration layer:
even before a row hits the database (where the NOT NULL constraint
catches `None`), the config object refuses to construct with an empty
or unset ``propagation_algorithm_version``.

The schema's NOT NULL constraint catches the `None` case at insert
time; the unit-level validation here catches *empty-string* regressions
that would slip past NOT NULL but still claim no propagator ran. This
is a regression test for the spec's "the sentinel must be a non-empty
documented marker" rule.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from scoring import PHASE_2_PROPAGATION_SENTINEL
from scoring.run import (
    PHASE_2_IMAGERY_WINDOW_SENTINEL,
    PHASE_2_PERCEPTION_MODEL_VERSION_SENTINEL,
    ScoringRunConfig,
    default_24_hourly_samples,
)


def _samples() -> tuple[datetime, ...]:
    return default_24_hourly_samples(date(2025, 6, 21))


def test_default_uses_phase_2_sentinel() -> None:
    """The default `ScoringRunConfig` writes the documented Phase-2 sentinel
    — not an empty string and not `None`."""
    config = ScoringRunConfig(temporal_samples=_samples(), osm_snapshot_date=date(2026, 5, 13))
    assert config.propagation_algorithm_version == PHASE_2_PROPAGATION_SENTINEL
    assert config.propagation_algorithm_version  # non-empty truthy guarantee
    assert config.perception_model_version == PHASE_2_PERCEPTION_MODEL_VERSION_SENTINEL
    assert config.imagery_capture_window == PHASE_2_IMAGERY_WINDOW_SENTINEL


def test_empty_propagation_version_rejected() -> None:
    """Passing an empty string for `propagation_algorithm_version`
    raises. Without this guard, the row would insert (empty string is
    NOT NULL-valid), falsely claiming no propagator while passing the
    schema invariant."""
    with pytest.raises(ValueError, match="propagation_algorithm_version"):
        ScoringRunConfig(
            temporal_samples=_samples(),
            osm_snapshot_date=date(2026, 5, 13),
            propagation_algorithm_version="",
        )


def test_empty_perception_model_version_rejected() -> None:
    with pytest.raises(ValueError, match="perception_model_version"):
        ScoringRunConfig(
            temporal_samples=_samples(),
            osm_snapshot_date=date(2026, 5, 13),
            perception_model_version="",
        )


def test_empty_imagery_capture_window_rejected() -> None:
    with pytest.raises(ValueError, match="imagery_capture_window"):
        ScoringRunConfig(
            temporal_samples=_samples(),
            osm_snapshot_date=date(2026, 5, 13),
            imagery_capture_window="",
        )


def test_empty_temporal_samples_rejected() -> None:
    with pytest.raises(ValueError, match="temporal_samples"):
        ScoringRunConfig(temporal_samples=(), osm_snapshot_date=date(2026, 5, 13))


def test_naive_datetime_in_temporal_samples_rejected() -> None:
    naive = datetime(2025, 6, 21, 12, 0)  # no tzinfo
    with pytest.raises(ValueError, match="not timezone-aware"):
        ScoringRunConfig(
            temporal_samples=(naive,),
            osm_snapshot_date=date(2026, 5, 13),
        )


def test_default_24_hourly_samples_returns_24_utc_timestamps() -> None:
    samples = default_24_hourly_samples(date(2025, 6, 21))
    assert len(samples) == 24
    assert all(s.tzinfo == UTC for s in samples)
    assert samples[0].hour == 0
    assert samples[-1].hour == 23
    # All within the same day.
    assert {s.date() for s in samples} == {date(2025, 6, 21)}
