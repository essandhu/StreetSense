"""Composite-risk assembly — Phase 4.6.8.

The composite risk surface combines the four real sub-scores with the
propagator's per-hour uplift output into a single per-(segment, hour)
number, plus its explainable decomposition into a *local contribution*
and a *propagation uplift*. The explainability invariant
(``CLAUDE.md`` and ``spec.md`` §"Explainability") requires both
components to be carried through the API and the frontend; this module
is the canonical place where they are assembled.

Formula (per Technical Note 1 of spec.md):

    composite_risk(seg, t) = local_aggregate(seg, t) + propagation_uplift(seg, t)

where

    local_aggregate(seg, t) =
        w_glare       * glare(seg, t)
      + w_lane        * lane_marking_quality(seg, t)
      + w_junction    * junction_complexity(seg)
      + w_historical  * historical_correlation(seg)

The default weights live below (and are surfaced through ADR 0006's
parameters); the orchestrator can override them via ScoringRunConfig.

All functions in this module are **pure-functional**: same inputs ->
same outputs, no I/O, no global state. Unit tests exercise the
arithmetic directly without standing up Postgres.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# Default sub-score weights for the local aggregate. Documented in
# ADR 0006 §"Parameter Defaults" and surfaced through
# ``ScoringRunConfig.composite_weights`` so an operator can sweep them
# without code changes.
DEFAULT_COMPOSITE_WEIGHTS: Mapping[str, float] = {
    "glare": 0.35,
    "lane_marking": 0.30,
    "junction_complexity": 0.20,
    "historical": 0.15,
}


@dataclass(frozen=True, slots=True)
class CompositeBreakdown:
    """A composite-risk row split into its named components.

    The API ships these as separate fields (per spec.md AC-7) so the
    user always sees *why* a segment is risky separately from *how
    much* network context is amplifying it.
    """

    composite_risk: float
    local_contribution: float
    propagation_uplift: float


def local_aggregate(
    sub_scores: Mapping[str, float],
    weights: Mapping[str, float] | None = None,
) -> float:
    """Weighted sum of the four sub-scores.

    ``sub_scores`` must contain every key in ``weights`` (and may
    contain extras; they're ignored). Missing required keys raise
    ``KeyError`` -- caller error, not a runtime fallback condition.

    Pure function; no I/O.
    """
    w = weights if weights is not None else DEFAULT_COMPOSITE_WEIGHTS
    total = 0.0
    for name, weight in w.items():
        total += weight * sub_scores[name]
    return total


def assemble(
    sub_scores: Mapping[str, float],
    propagation_uplift: float,
    weights: Mapping[str, float] | None = None,
) -> CompositeBreakdown:
    """Compose one ``(local_aggregate, propagation_uplift) -> composite_risk`` row.

    The composite is the sum of the two components. Surfacing both
    components separately preserves the explainability invariant
    (CLAUDE.md): the user can decompose the composite into
    (local-glare + local-lane + local-junction + local-historical +
    propagation-uplift) without computing anything themselves.
    """
    local = local_aggregate(sub_scores, weights)
    return CompositeBreakdown(
        composite_risk=local + propagation_uplift,
        local_contribution=local,
        propagation_uplift=propagation_uplift,
    )


__all__ = [
    "DEFAULT_COMPOSITE_WEIGHTS",
    "CompositeBreakdown",
    "assemble",
    "local_aggregate",
]
