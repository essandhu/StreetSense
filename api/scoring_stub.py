"""Phase 1 stub risk scorer.

A pure function of `segment_id`, deterministic across reloads. Phase 2/3/4
replace this with real environmental, perception, and propagated scores —
but the API response shape (composite + four sub-scores + confidence) is
stable from day one (Technical Note 7 in spec.md).

The values returned here are **non-meaningful**. Every API response that
uses them sets `risk_stub: true` so consumers cannot mistake them for real
scores.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import xxhash


@dataclass(frozen=True, slots=True)
class StubSubScores:
    """The four sub-scores carried in every composite-risk response.

    Names mirror the per-segment-score interface that real scorers will
    implement in Phases 2-4. Phase 1 fills in stub values; the shape is
    contract.
    """

    lane_marking_quality: float
    glare_exposure: float
    junction_complexity: float
    historical_correlation: float


@dataclass(frozen=True, slots=True)
class StubRiskScore:
    composite: float
    sub_scores: StubSubScores
    confidence: float
    risk_stub: bool = True


# Salts so the four sub-scores derive from independent hash domains. If they
# all hashed the same input identically, sub-scores would correlate
# deterministically and tests would not catch a bug where a Phase 2 scorer
# accidentally returned the composite for every sub-score.
_SALT_LANE = b"lane-marking"
_SALT_GLARE = b"glare-exposure"
_SALT_JUNCTION = b"junction-complexity"
_SALT_HISTORICAL = b"historical-correlation"
_SALT_COMPOSITE = b"composite"


def _hash_to_unit(salt: bytes, segment_id: UUID) -> float:
    """Hash (salt, segment_id) to a uniform float in [0, 1]."""
    digest = xxhash.xxh64()
    digest.update(salt)
    digest.update(segment_id.bytes)
    # 64-bit unsigned → [0, 1) by dividing by 2**64.
    return digest.intdigest() / (1 << 64)


def stub_risk(segment_id: UUID) -> StubRiskScore:
    """Deterministic stub risk for a segment.

    Args:
        segment_id: Stable per-segment UUID. Same input → same output, in
            this process and across restarts.

    Returns:
        A `StubRiskScore` where every value is a function of `segment_id`
        and a per-field salt. `risk_stub` is always True.
    """
    sub = StubSubScores(
        lane_marking_quality=_hash_to_unit(_SALT_LANE, segment_id),
        glare_exposure=_hash_to_unit(_SALT_GLARE, segment_id),
        junction_complexity=_hash_to_unit(_SALT_JUNCTION, segment_id),
        historical_correlation=_hash_to_unit(_SALT_HISTORICAL, segment_id),
    )
    return StubRiskScore(
        composite=_hash_to_unit(_SALT_COMPOSITE, segment_id),
        sub_scores=sub,
        confidence=0.0,  # Stub: no confidence semantics in Phase 1.
        risk_stub=True,
    )


def stub_risk_bucket(segment_id: UUID, n_buckets: int = 5) -> int:
    """Five-step palette bucket for the GPU-side color expression.

    Mirrors the SQL VIEW Phase 1.5.6 publishes to pg_tileserv so the
    frontend can color segments without a per-frame JS computation.
    """
    composite = _hash_to_unit(_SALT_COMPOSITE, segment_id)
    return min(int(composite * n_buckets), n_buckets - 1)
