"""Pydantic models for the API surface.

Every model crossing a process boundary is Pydantic. Sub-score fields are
*always* present in any composite-risk response — Phase 2 ships glare as
the first real sub-score; the other three carry ``is_stub: true`` until
later phases. Phase 1's top-level ``risk_stub: bool`` is **removed**:
per-sub-score ``is_stub`` flags replace it. This is the breaking API
change that bumps the OpenAPI ``info.version`` from 1.0 to 2.0.

Branded UUID types: `SegmentId`, `RunId`. They are typed `UUID` at runtime;
the type alias gives a strong handle when reading IDE / mypy output.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, NewType
from uuid import UUID

from pydantic import BaseModel, Field

SegmentId = NewType("SegmentId", UUID)
RunId = NewType("RunId", UUID)


class SubScore(BaseModel):
    """One sub-score's per-segment, per-timestamp output as exposed at
    the API boundary.

    Mirrors `scoring.interface.SubScoreResult` but lives in `api/` so
    the API can evolve its serialization independently if needed."""

    value: float = Field(..., ge=0.0, le=1.0, description="Risk in [0, 1]. Higher = worse.")
    confidence: float = Field(..., ge=0.0, le=1.0)
    is_stub: bool = Field(
        ...,
        description=(
            "True if the value is a stub (no real scorer for this sub-score "
            "in this phase). Consumers must check this — Phase 2 carries "
            "is_stub=false only for glare_exposure."
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Scorer-specific provenance (e.g., sun_azimuth_deg / "
            "sun_elevation_deg for glare). Safe to add fields without "
            "breaking consumers."
        ),
    )


class SubScores(BaseModel):
    """Four sub-scores carried in every composite-risk response.

    Phase 1 stub values. Phase 2 fills `glare_exposure`; Phase 3 fills
    `lane_marking_quality` and `historical_correlation`; Phase 4 fills the
    weighted composite. `junction_complexity` is derived from OSM topology
    in Phase 3+.
    """

    lane_marking_quality: SubScore
    glare_exposure: SubScore
    junction_complexity: SubScore
    historical_correlation: SubScore


class SegmentDetail(BaseModel):
    """Per-segment detail payload returned by GET /segments/{id}.

    Phase 2: top-level `risk_stub` flag removed; per-sub-score
    `is_stub` flags inside `sub_scores` replace it.
    """

    segment_id: UUID
    osm_way_id: int | None
    composite_risk: float = Field(
        ...,
        description=(
            "Composite risk in [0, 1]. Phase 2: equal to the glare value "
            "(the only real sub-score). Phase 4 ships the weighted composite."
        ),
    )
    sub_scores: SubScores
    confidence: float = Field(
        ...,
        description="Confidence in [0, 1]. Phase 2: glare's confidence (placeholder 1.0).",
    )
    attrs: dict[str, str] = Field(default_factory=dict, description="Selected OSM attributes.")


class FreshnessEntry(BaseModel):
    """One row of `/admin/freshness`."""

    name: str
    last_ingested_at: datetime | None
    metadata: dict[str, object] = Field(default_factory=dict)


class FreshnessReport(BaseModel):
    """Response shape for `/admin/freshness`.

    A *list* (wrapped) — not a single object — so Phase 3 can register
    multiple data sources (imagery providers, incident feeds) without a
    breaking API change.
    """

    sources: list[FreshnessEntry]
    server_time: datetime


class ScoringRunMetadata(BaseModel):
    """Provenance carried with any score-bearing response.

    Phase 1 sets `perception_model_version`, `imagery_capture_window`, and
    `propagation_algorithm_version` to stable stub values so the shape is
    locked. Real values arrive in later phases.
    """

    scoring_run_id: RunId
    scoring_run_timestamp: datetime
    perception_model_version: str
    osm_snapshot_date: date
    imagery_capture_window_start: date
    imagery_capture_window_end: date
    propagation_algorithm_version: str
