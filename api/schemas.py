"""Pydantic models for the API surface.

Every model crossing a process boundary is Pydantic.

API version history:

- 1.0 (Phase 1): per-segment composite + a single ``risk_stub`` flag.
- 2.0 (Phase 2): per-sub-score ``is_stub`` replaces the top-level flag;
  scalar ``confidence`` populated by the glare scorer.
- 3.0 (Phase 3, **breaking**): ``confidence`` reshapes from a scalar
  float to a ``ConfidenceIndicator`` object so the UI can label which
  input is the limiter; a new ``imagery`` list ships in
  ``SegmentDetail`` carrying pre-signed MinIO URLs for source imagery.
- 4.0 (Phase 4, **non-breaking add**): ``SegmentDetail`` gains
  ``propagation_uplift``, ``local_contribution``, and a typed
  ``propagation_algorithm`` block so the API can ship the
  composite-risk decomposition (per spec.md §"Explainability"). All
  four sub-scores carry ``is_stub=False`` in steady-state Phase 4
  responses (the registry's ``real_since_phase`` bumped to 4 for the
  two new scorers).
- 5.0 (Phase 5, **non-breaking add**): ``SubScoreDeltas``,
  ``SegmentDelta``, and ``DeltaResponse`` ship to back the new
  ``GET /runs/{run_a}/delta/{run_b}`` endpoint. The shape mirrors
  Phase 4's composite-risk decomposition — every delta row carries
  ``composite_delta = local_contribution_delta + propagation_uplift_delta``
  alongside per-sub-score deltas and *both* runs'
  ``ConfidenceIndicator`` so the UI can label how confident each end
  of the comparison was. Existing endpoints are unaffected.

Branded UUID types: `SegmentId`, `RunId`. Typed ``UUID`` at runtime; the
type alias gives a strong handle when reading IDE / mypy output.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, NewType
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


class ConfidenceIndicator(BaseModel):
    """Per-segment confidence + the input that limited it (spec Tech Note 4).

    ``value`` is the min-rule combination of ``freshness``, ``coverage``,
    and ``1 - model_uncertainty``. ``limiter`` names whichever of the
    three drove the value lowest.
    """

    value: float = Field(..., ge=0.0, le=1.0)
    limiter: Literal["freshness", "coverage", "model"]


class ImageryReference(BaseModel):
    """A pre-signed pointer to one piece of source imagery."""

    url: str = Field(
        ...,
        description=(
            "Pre-signed MinIO URL with a short TTL. Clients fetch the bytes "
            "directly from MinIO rather than proxying through the API."
        ),
    )
    provider: str
    capture_date: date
    heading_deg: float
    camera_params: dict[str, Any] = Field(default_factory=dict)


class PropagationAlgorithmInfo(BaseModel):
    """Composite-risk propagator identity + parameters.

    Surfaced in :class:`SegmentDetail` so the UI can label the
    composite-breakdown panel with the algorithm name + semver. Reads
    from ``scoring_runs.propagation_algorithm_version`` (Phase 4
    onwards a real ``"<name>-<semver>"`` string; pre-Phase-4 rows
    carry the ``"none-phase-2"`` sentinel).
    """

    name: str = Field(
        ...,
        description=(
            'Stable algorithm identifier (e.g., ``"pagerank-diffusion"``). '
            "Empty when the row predates Phase 4 (sentinel branch)."
        ),
    )
    version: str = Field(
        ...,
        description=(
            'Algorithm semver (e.g., ``"0.1.0"``). Combined with ``name`` '
            "this reconstructs the persisted ``propagation_algorithm_version`` "
            "string."
        ),
    )


class SegmentDetail(BaseModel):
    """Per-segment detail payload returned by GET /segments/{id}.

    Phase 3 (API 3.0, breaking): ``confidence`` is a
    ``ConfidenceIndicator`` object (not a scalar); ``imagery`` carries
    pre-signed URLs for the source images that backed
    ``sub_scores.lane_marking_quality``.

    Phase 4 (API 4.0, non-breaking add): ``propagation_uplift`` and
    ``local_contribution`` split ``composite_risk`` into its
    explainable components; ``propagation_algorithm`` carries the
    propagator identity + semver from the most recent scoring run.
    """

    segment_id: UUID
    osm_way_id: int | None
    composite_risk: float = Field(
        ...,
        description=(
            "Composite risk. Phase 4: ``local_contribution + "
            "propagation_uplift``. The upper bound depends on the active "
            "composite weights (recorded in ADR 0006's parameters)."
        ),
    )
    local_contribution: float = Field(
        0.0,
        description=(
            "Weighted local aggregate of the four sub-scores at this "
            "(segment, t). Phase 4: the same quantity the propagator "
            "received as the per-node input vector."
        ),
    )
    propagation_uplift: float = Field(
        0.0,
        description=(
            "Network contribution to composite risk — the portion that "
            "would not exist without the propagator. Phase 4: read "
            "directly from ``segment_scores.propagation_uplift``."
        ),
    )
    propagation_algorithm: PropagationAlgorithmInfo | None = Field(
        None,
        description=(
            "Identity of the propagator that produced ``propagation_uplift``. "
            "``None`` for pre-Phase 4 rows where the persisted "
            '``propagation_algorithm_version`` is the sentinel ``"none-phase-2"``.'
        ),
    )
    sub_scores: SubScores
    confidence: ConfidenceIndicator
    imagery: list[ImageryReference] = Field(
        default_factory=list,
        description=(
            "Source imagery references backing this segment's perception "
            "sub-score. Empty when no imagery is available."
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


# -- Phase 5 — Delta schemas -----------------------------------------------
#
# Field-by-field deltas (run_b - run_a) carried by the new
# ``GET /runs/{run_a}/delta/{run_b}`` endpoint. The decomposition invariant
# (CLAUDE.md §"Explainability") carries through to the delta path:
# ``composite_delta == local_contribution_delta + propagation_uplift_delta``,
# enforced by the pure-functional delta computation (Task 2.2) and asserted
# by its property tests. Field names mirror :class:`SubScores` /
# :class:`SegmentDetail` so a frontend iterating fields sees the same order
# in both single-run and delta responses.


class SubScoreDeltas(BaseModel):
    """Per-sub-score delta values (``run_b - run_a``).

    Field names and order match :class:`SubScores` exactly. Values may be
    negative (risk went down between runs). The frontend's largest-changes
    list typically sorts by ``abs(composite_delta)``, then surfaces the
    per-sub-score deltas as the explanation.
    """

    lane_marking_quality: float
    glare_exposure: float
    junction_complexity: float
    historical_correlation: float


class SegmentDelta(BaseModel):
    """One per-segment delta row.

    The explainability invariant adapted for delta-land: every row carries
    the full composite decomposition (``composite_delta`` plus the two
    parts it decomposes into) and *both* run's confidence indicators so
    the UI can show how confident each end of the comparison was.

    Convention: positive ``composite_delta`` means risk went up from
    ``run_a`` to ``run_b``. Negative means risk went down.
    """

    segment_id: UUID
    composite_delta: float = Field(
        ...,
        description=(
            "Composite-risk delta (``run_b.composite_risk - "
            "run_a.composite_risk``). Equal to "
            "``local_contribution_delta + propagation_uplift_delta`` by "
            "construction."
        ),
    )
    local_contribution_delta: float = Field(
        ...,
        description="Delta in the weighted local sub-score aggregate.",
    )
    propagation_uplift_delta: float = Field(
        ...,
        description=("Delta in the network-contribution portion of composite risk."),
    )
    sub_score_deltas: SubScoreDeltas
    confidence_a: ConfidenceIndicator = Field(
        ..., description="Confidence indicator from ``run_a``."
    )
    confidence_b: ConfidenceIndicator = Field(
        ..., description="Confidence indicator from ``run_b``."
    )


class DeltaResponse(BaseModel):
    """Response payload for ``GET /runs/{run_a}/delta/{run_b}``.

    Both runs' provenance bundles ship with the response so the frontend
    never has to fetch them separately to render the delta view.
    Pagination fields are required — a city-scale delta can contain tens
    of thousands of segments and clients page through them.
    """

    run_a: ScoringRunMetadata
    run_b: ScoringRunMetadata
    deltas: list[SegmentDelta]
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1)
    total: int = Field(..., ge=0, description="Total number of delta rows across all pages.")
