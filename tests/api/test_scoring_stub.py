"""Unit tests for `api.scoring_stub` — Task 1.5.5.

The stub must be deterministic (same input → same output) and must produce
all four sub-scores in [0, 1].
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from api.scoring_stub import stub_risk, stub_risk_bucket


def test_stub_risk_is_deterministic() -> None:
    sid = UUID("11111111-1111-1111-1111-111111111111")
    a = stub_risk(sid)
    b = stub_risk(sid)
    assert a == b


def test_stub_risk_components_in_unit_interval() -> None:
    sid = uuid4()
    score = stub_risk(sid)
    assert 0.0 <= score.composite < 1.0
    assert 0.0 <= score.sub_scores.lane_marking_quality < 1.0
    assert 0.0 <= score.sub_scores.glare_exposure < 1.0
    assert 0.0 <= score.sub_scores.junction_complexity < 1.0
    assert 0.0 <= score.sub_scores.historical_correlation < 1.0


def test_stub_risk_marks_itself_as_stub() -> None:
    assert stub_risk(uuid4()).risk_stub is True


def test_different_segments_get_different_scores() -> None:
    a = stub_risk(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
    b = stub_risk(UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
    assert a.composite != b.composite


def test_sub_scores_are_independently_derived() -> None:
    """If a future bug made all four sub-scores share an internal hash, this
    test would catch it: a randomly chosen segment is overwhelmingly likely
    to produce four different sub-scores when the salts work correctly."""
    sid = uuid4()
    score = stub_risk(sid)
    values = (
        score.sub_scores.lane_marking_quality,
        score.sub_scores.glare_exposure,
        score.sub_scores.junction_complexity,
        score.sub_scores.historical_correlation,
    )
    # Distinct under a deterministic salt — collisions are negligible.
    assert len(set(values)) == 4


@pytest.mark.parametrize("n", [3, 5, 10])
def test_stub_bucket_in_range(n: int) -> None:
    bucket = stub_risk_bucket(uuid4(), n_buckets=n)
    assert 0 <= bucket < n
