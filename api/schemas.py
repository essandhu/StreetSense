"""Pydantic models for the API surface.

Every model crossing a process boundary is Pydantic. Sub-score fields are
*always* present in any composite-risk response — Phase 1 fills them with
deterministic stub values, but the shape is contract.

Branded UUID types: `SegmentId`, `RunId`. They are typed `UUID` at runtime;
the type alias gives a strong handle when reading IDE / mypy output.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import NewType
from uuid import UUID

from pydantic import BaseModel, Field

SegmentId = NewType("SegmentId", UUID)
RunId = NewType("RunId", UUID)


class SubScores(BaseModel):
    """Four sub-scores carried in every composite-risk response.

    Phase 1 stub values. Phase 2 fills `glare_exposure`; Phase 3 fills
    `lane_marking_quality` and `historical_correlation`; Phase 4 fills the
    weighted composite. `junction_complexity` is derived from OSM topology
    in Phase 3+.
    """

    lane_marking_quality: float = Field(
        ..., description="Lane marking degradation risk in [0, 1]. Higher = worse."
    )
    glare_exposure: float = Field(
        ..., description="Sun-glare exposure risk in [0, 1] for the configured time window."
    )
    junction_complexity: float = Field(
        ..., description="Junction topology complexity in [0, 1]. Higher = more complex."
    )
    historical_correlation: float = Field(
        ..., description="Correlation with historical incident clusters in [0, 1]."
    )


class SegmentDetail(BaseModel):
    """Per-segment detail payload returned by GET /segments/{id}."""

    segment_id: UUID
    osm_way_id: int | None
    composite_risk: float = Field(
        ..., description="Composite risk in [0, 1]. Phase 1: stub. See risk_stub."
    )
    sub_scores: SubScores
    confidence: float = Field(
        ...,
        description="Confidence in [0, 1]. Phase 1: stub (0.0). Real values arrive Phase 4.",
    )
    risk_stub: bool = Field(
        ...,
        description=(
            "True if composite_risk / sub_scores are stub values. "
            "Consumers must check this — Phase 1 always returns true."
        ),
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
