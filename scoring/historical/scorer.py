"""Historical-correlation scorer — Phase 4.5.13.

Implements the :class:`SubScorer` protocol from ``scoring.interface``.
Per-segment score in [0, 1] derived from a recency-weighted kernel
density estimate over historical incidents (from the `incidents`
PostGIS table, populated by the MassDOT IMPACT adapter per ADR 0007).

Algorithm:
  1. For each segment, locate incidents within ``radius_m`` of the
     segment's representative point.
  2. Weight each incident by a Gaussian spatial kernel + exponential
     recency decay:
         w_i = exp(-d_i^2 / (2 * sigma^2)) * exp(-age_i / half_life)
     where:
         d_i      = distance in meters from the segment to incident i
         sigma    = radius_m / 2 (kernel falls off rapidly past radius)
         age_i    = (run_at - incident_at) in days
         half_life = configurable; default 365 days
  3. Sum the weights and normalize by a per-scoring-run city-wide
     maximum so the final score is in [0, 1].

The scorer is decoupled from the data layer through an
``incident_loader`` callable: callers decide whether incidents come
from the PostGIS table (production) or pre-loaded fixtures (unit
tests). The normalization maximum is supplied by the orchestrator (it
sweeps every segment before normalizing -- the loader pattern allows
that ordering without coupling the scorer to global state).

Time-invariance: ``score_for_samples(segment, ats)`` returns the same
value for every timestamp -- incident history is a static input
snapshot at scoring-run time, and the recency decay is computed
against the scoring run's reference timestamp (passed via
construction, not via ``at``).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID

from scoring.interface import ScoringSegment, SubScoreResult


@dataclass(frozen=True, slots=True)
class IncidentNearby:
    """An incident within scoring range of a segment.

    Fields are the minimum surface the scorer needs; the full
    `incidents` row carries more (severity, metadata).
    """

    incident_id: UUID
    distance_m: float
    incident_at: datetime
    # Severity is mapped to a multiplicative weight: fatal = 3.0,
    # injury = 2.0, PDO = 1.0, unknown = 1.0. Multiplier defaulted to
    # 1.0 if absent so the loader can omit severity for synthetic
    # fixtures.
    severity_weight: float = 1.0


# Loader contract: given a segment, return the incidents within
# radius_m. The radius is passed so the loader can do an efficient
# ST_DWithin query rather than fetching everything and filtering in
# Python.
IncidentLoader = Callable[[ScoringSegment, float], Sequence[IncidentNearby]]


# Defaults documented in ADR 0006's parameter discussion.
DEFAULT_RADIUS_M = 50.0
DEFAULT_RECENCY_HALF_LIFE_DAYS = 365.0


def _seconds_per_day() -> float:
    return 86400.0


def compute_raw_weight(
    distance_m: float,
    incident_at: datetime,
    run_at: datetime,
    *,
    radius_m: float = DEFAULT_RADIUS_M,
    half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS,
    severity_weight: float = 1.0,
) -> float:
    """Per-incident weight: Gaussian spatial * exponential recency * severity.

    Pure function; no I/O. Used internally by the scorer and exposed
    for unit testing.
    """
    sigma = radius_m / 2.0
    spatial = math.exp(-(distance_m * distance_m) / (2.0 * sigma * sigma))

    age_days = (run_at - incident_at).total_seconds() / _seconds_per_day()
    # Negative age (incident reported after run_at) clamps to 0 so the
    # weight doesn't blow up.
    age_days = max(0.0, age_days)
    # Half-life semantics: weight halves every `half_life_days`. Equivalent
    # to exp(-ln(2) * age / half_life). Using 0.5 ** (...) is numerically
    # identical at machine precision.
    recency = (0.5 ** (age_days / half_life_days)) if half_life_days > 0 else 0.0

    return spatial * recency * severity_weight


class HistoricalCorrelationScorer:
    """SubScorer producing per-segment historical-incident-density scores.

    Constructed with an ``incident_loader`` callable + a per-run
    ``run_at`` reference timestamp + an optional per-run ``max_weight``
    for normalization. The orchestrator typically sweeps every
    segment once to discover the city-wide max, then passes that max
    back to the scorer for the per-segment normalization pass.

    If ``max_weight`` is None (e.g., during unit tests or for the
    discovery pass), the scorer returns the raw (un-normalized) sum
    clipped to [0, 1] -- callers can detect this via
    ``SubScoreResult.metadata['normalized']``.
    """

    name: ClassVar[str] = "historical"

    def __init__(
        self,
        incident_loader: IncidentLoader,
        *,
        run_at: datetime,
        radius_m: float = DEFAULT_RADIUS_M,
        half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS,
        max_weight: float | None = None,
    ) -> None:
        if run_at.tzinfo is None:
            msg = "run_at must be timezone-aware"
            raise ValueError(msg)
        self._incident_loader = incident_loader
        self._run_at = run_at.astimezone(UTC)
        self._radius_m = radius_m
        self._half_life_days = half_life_days
        self._max_weight = max_weight

    def raw_weight_sum(self, segment: ScoringSegment) -> float:
        """Sum of per-incident weights for ``segment``. Pre-normalization.

        Exposed for the orchestrator's city-wide max-discovery pass.
        """
        total = 0.0
        for incident in self._incident_loader(segment, self._radius_m):
            total += compute_raw_weight(
                distance_m=incident.distance_m,
                incident_at=incident.incident_at,
                run_at=self._run_at,
                radius_m=self._radius_m,
                half_life_days=self._half_life_days,
                severity_weight=incident.severity_weight,
            )
        return total

    def score(self, segment: ScoringSegment, *, at: datetime) -> SubScoreResult:
        """Compute the per-segment historical-correlation score.

        Time-invariant: ``at`` is unused. Recency decay is computed
        against ``run_at`` (passed at construction), not ``at``, so
        the same scorer reused across hours within a run produces
        the same value per segment.
        """
        del at  # documented unused
        raw = self.raw_weight_sum(segment)

        if self._max_weight is not None and self._max_weight > 0.0:
            value = min(1.0, raw / self._max_weight)
            normalized = True
        else:
            # Discovery pass / unit-test fallback: clip the raw sum
            # to [0, 1] without external normalization. Useful for
            # tests that don't care about cross-segment scale.
            value = min(1.0, raw)
            normalized = False

        return SubScoreResult(
            value=value,
            confidence=1.0,
            is_stub=False,
            metadata={
                "raw_weight_sum": raw,
                "normalized": normalized,
                "radius_m": self._radius_m,
                "half_life_days": self._half_life_days,
                "run_at": self._run_at.isoformat(),
            },
        )

    def score_for_samples(
        self,
        segment: ScoringSegment,
        ats: Sequence[datetime],
    ) -> list[SubScoreResult]:
        """Batched variant returning the same value for every timestamp."""
        if not ats:
            return []
        result = self.score(segment, at=ats[0])
        return [result] * len(ats)


__all__ = [
    "DEFAULT_RADIUS_M",
    "DEFAULT_RECENCY_HALF_LIFE_DAYS",
    "HistoricalCorrelationScorer",
    "IncidentLoader",
    "IncidentNearby",
    "compute_raw_weight",
]
