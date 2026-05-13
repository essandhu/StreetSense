"""StreetSense scoring layer.

Phase-spanning constants and re-exports live here. Per-scorer
implementations live in the subpackages (`environmental/`, `perception/`,
`propagator/`).
"""

from __future__ import annotations

# Sentinel `propagation_algorithm_version` written into `segment_scores`
# rows produced by Phase 2 scoring runs. The reproducibility invariant
# (CLAUDE.md, spec.md §"Non-Negotiable Invariants") requires every score
# row to populate the column, but Phase 2 ships no real propagator —
# Phase 4 does. The sentinel is a deliberate, non-empty marker so the
# schema's NOT NULL constraint stays satisfied without silently claiming
# a propagator ran.
PHASE_2_PROPAGATION_SENTINEL = "none-phase-2"


__all__ = ["PHASE_2_PROPAGATION_SENTINEL"]
