"""Per-segment-score interface — extension point 1.

A `SubScorer` is the unit of pluggable risk evaluation. Phase 2's glare
scorer is the first concrete implementation; Phase 3's perception scorer
attaches by implementing this `Protocol` alone, with no changes to the
scoring-run orchestration or the tile API.

Three types are public:

- `ScoringSegment` — the inputs a scorer needs, decoupled from ingestion's
  `RoadSegment`. Carries the segment's UUID (assigned at persist time), a
  representative lat/lon for solar-position lookups, and the road heading
  in degrees from true north.
- `SubScoreResult` — the scorer's per-call output. A value in [0, 1], a
  confidence, an `is_stub` flag, and a free-form `metadata` dict for
  scorer-specific outputs (sun azimuth/elevation for glare, etc.).
- `SubScorer` (Protocol) — the contract. `score(segment, *, at) ->
  SubScoreResult`. Pure-functional: same inputs → same output, no I/O,
  no global state.

Validation invariants (enforced by Pydantic on `SubScoreResult`):
    - `0.0 <= value <= 1.0`
    - `0.0 <= confidence <= 1.0`
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScoringSegment(BaseModel):
    """Inputs a `SubScorer` consumes per segment.

    Decoupled from `ingestion.osm.RoadSegment` so the scoring layer does
    not depend on the ingestion adapter shape. The scoring-run
    orchestration (Phase 2.3) derives `heading_deg` and the representative
    point from the persisted `road_segments.geometry` at fetch time.
    """

    model_config = ConfigDict(frozen=True)

    segment_id: UUID
    heading_deg: float = Field(
        ...,
        ge=0.0,
        lt=360.0,
        description="Driver-facing road heading in degrees clockwise from true north.",
    )
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)


class SubScoreResult(BaseModel):
    """One scorer's per-segment, per-timestamp output."""

    model_config = ConfigDict(frozen=True)

    value: float = Field(..., ge=0.0, le=1.0, description="Risk in [0, 1]. Higher = worse.")
    confidence: float = Field(..., ge=0.0, le=1.0)
    is_stub: bool = Field(
        ...,
        description=(
            "True if the scorer has not been wired to real inputs for this row. "
            "Per the explainability invariant, consumers see this flag per sub-score."
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Scorer-specific provenance (e.g., sun azimuth/elevation for glare). "
            "Not part of the value/confidence contract; safe to add fields without "
            "breaking consumers."
        ),
    )


class SubScorer(Protocol):
    """The per-segment-score contract — extension point 1.

    Implementations are pure functions of `(segment, at)`. They do not
    perform I/O, do not hold module-level caches keyed on inputs, and do
    not consult `datetime.now()`. Determinism is enforced by property
    tests in the scorer's own test module.
    """

    name: str
    """Stable identifier for the scorer (e.g., ``"glare"``). Used by the
    scoring run to route results to the correct ``segment_scores`` column
    and to set the matching ``is_stub_*`` flag."""

    def score(self, segment: ScoringSegment, *, at: datetime) -> SubScoreResult:
        """Compute the sub-score for ``segment`` at UTC time ``at``."""
        ...


__all__ = ["ScoringSegment", "SubScoreResult", "SubScorer"]
