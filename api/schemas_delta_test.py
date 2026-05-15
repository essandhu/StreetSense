"""Shape tests for the Phase-5 delta schemas (Task 2.1).

These are *schema-only* tests — round-trip serialization, field validation,
and the explainability invariant that every per-segment delta row carries
both confidence indicators (so the UI can label *how confident each end of
the comparison was*).

The pure-functional delta computation is tested in
``api/delta_test.py`` (Task 2.2) using ``hypothesis`` property tests.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from api.schemas import (
    ConfidenceIndicator,
    DeltaResponse,
    ScoringRunMetadata,
    SegmentDelta,
    SubScoreDeltas,
)


def _confidence(value: float = 0.8) -> ConfidenceIndicator:
    return ConfidenceIndicator(value=value, limiter="coverage")


def _run_metadata(run_id: UUID | None = None) -> ScoringRunMetadata:
    return ScoringRunMetadata(
        scoring_run_id=run_id or uuid4(),  # type: ignore[arg-type]
        scoring_run_timestamp=datetime(2026, 5, 15, 3, 0, 0),
        perception_model_version="stand-in-onnx-0.1.0",
        osm_snapshot_date=date(2026, 5, 1),
        imagery_capture_window_start=date(2025, 11, 1),
        imagery_capture_window_end=date(2026, 5, 1),
        propagation_algorithm_version="pagerank-diffusion-0.1.0",
    )


def _sub_score_deltas(**overrides: float) -> SubScoreDeltas:
    defaults: dict[str, float] = {
        "lane_marking_quality": 0.05,
        "glare_exposure": -0.02,
        "junction_complexity": 0.0,
        "historical_correlation": 0.01,
    }
    defaults.update(overrides)
    return SubScoreDeltas(**defaults)


# -- SubScoreDeltas --------------------------------------------------------


def test_sub_score_deltas_roundtrip() -> None:
    """SubScoreDeltas dumps and reloads with the same values."""
    deltas = _sub_score_deltas()
    reloaded = SubScoreDeltas.model_validate(deltas.model_dump())
    assert reloaded == deltas


def test_sub_score_deltas_allows_negative_values() -> None:
    """Sub-score deltas may be negative (risk went down between runs)."""
    deltas = _sub_score_deltas(lane_marking_quality=-0.3)
    assert deltas.lane_marking_quality == -0.3


def test_sub_score_deltas_requires_all_four_fields() -> None:
    """All four sub-score names are required — partial dicts are rejected."""
    with pytest.raises(ValidationError):
        SubScoreDeltas.model_validate({"lane_marking_quality": 0.1})


def test_sub_score_deltas_field_order_matches_sub_scores() -> None:
    """The four names match the existing SubScores model so a frontend
    iterating fields sees them in the same order in both single-run and
    delta responses."""
    from api.schemas import SubScores

    sub_score_fields = list(SubScores.model_fields.keys())
    delta_fields = list(SubScoreDeltas.model_fields.keys())
    assert sub_score_fields == delta_fields


# -- SegmentDelta ----------------------------------------------------------


def test_segment_delta_requires_all_fields() -> None:
    """Per the explainability invariant, every delta row carries the
    composite decomposition AND both confidence indicators."""
    with pytest.raises(ValidationError):
        SegmentDelta.model_validate({"segment_id": str(uuid4())})


def test_segment_delta_roundtrip() -> None:
    delta = SegmentDelta(
        segment_id=uuid4(),
        composite_delta=0.12,
        local_contribution_delta=0.08,
        propagation_uplift_delta=0.04,
        sub_score_deltas=_sub_score_deltas(),
        confidence_a=_confidence(0.7),
        confidence_b=_confidence(0.9),
    )
    reloaded = SegmentDelta.model_validate(delta.model_dump())
    assert reloaded == delta


def test_segment_delta_allows_zero_composite_delta() -> None:
    """A segment whose composite didn't move still gets a row (zero delta)
    — the frontend filters; the API doesn't pre-filter on magnitude."""
    delta = SegmentDelta(
        segment_id=uuid4(),
        composite_delta=0.0,
        local_contribution_delta=0.0,
        propagation_uplift_delta=0.0,
        sub_score_deltas=_sub_score_deltas(
            lane_marking_quality=0.0,
            glare_exposure=0.0,
            junction_complexity=0.0,
            historical_correlation=0.0,
        ),
        confidence_a=_confidence(),
        confidence_b=_confidence(),
    )
    assert delta.composite_delta == 0.0


def test_segment_delta_rejects_non_float_composite() -> None:
    with pytest.raises(ValidationError):
        SegmentDelta.model_validate(
            {
                "segment_id": str(uuid4()),
                "composite_delta": "not-a-number",
                "local_contribution_delta": 0.0,
                "propagation_uplift_delta": 0.0,
                "sub_score_deltas": _sub_score_deltas().model_dump(),
                "confidence_a": _confidence().model_dump(),
                "confidence_b": _confidence().model_dump(),
            }
        )


# -- DeltaResponse ---------------------------------------------------------


def test_delta_response_carries_both_run_metadata_bundles() -> None:
    """Both run provenance bundles ship with the response so the
    frontend never has to fetch them separately to render the delta view.

    This is the reproducibility invariant adapted for the delta path: the
    six fields from CLAUDE.md ship for *both* sides of the comparison.
    """
    run_a = _run_metadata()
    run_b = _run_metadata()
    response = DeltaResponse(
        run_a=run_a,
        run_b=run_b,
        deltas=[],
        page=1,
        page_size=100,
        total=0,
    )
    assert response.run_a == run_a
    assert response.run_b == run_b


def test_delta_response_empty_list_is_valid() -> None:
    """Two runs with no overlapping segments still produce a well-formed
    response with an empty ``deltas`` list."""
    response = DeltaResponse(
        run_a=_run_metadata(),
        run_b=_run_metadata(),
        deltas=[],
        page=1,
        page_size=100,
        total=0,
    )
    assert response.deltas == []


def test_delta_response_pagination_fields_required() -> None:
    """Pagination fields are part of the contract — a 50k-segment city
    needs paging or the response stalls the frontend."""
    with pytest.raises(ValidationError):
        DeltaResponse.model_validate(
            {
                "run_a": _run_metadata().model_dump(mode="json"),
                "run_b": _run_metadata().model_dump(mode="json"),
                "deltas": [],
            }
        )
