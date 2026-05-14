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
#
# Phase 3 retains this exact sentinel value (no rename to ``"none-phase-3"``).
# The string identifies *which version of the codebase* introduced it
# rather than which scoring run last touched the row; renaming it on every
# phase would be churn that breaks regression queries spanning runs.
PHASE_2_PROPAGATION_SENTINEL = "none-phase-2"


# Documentation-only constant recording the phase that retires
# ``PHASE_2_PROPAGATION_SENTINEL`` above. Phase 4's scoring run writes the real
# ``<algo-name>-<semver>`` value into ``propagation_algorithm_version`` (e.g.,
# ``"influence-diffusion-0.1.0"``); ``"none-phase-2"`` becomes a value no
# Phase 4+ scoring run ever produces. Pre-Phase-4 rows still carry the
# sentinel for code archaeology -- they were never wrong, the propagator just
# didn't run.
#
# This constant is consumed by ``tests/scoring/test_phase2_sentinel_retired.py``
# (added in Phase 4.6.6) to assert the retirement contract.
PHASE_2_PROPAGATION_SENTINEL_RETIRED_IN = "4"


__all__ = [
    "PHASE_2_PROPAGATION_SENTINEL",
    "PHASE_2_PROPAGATION_SENTINEL_RETIRED_IN",
]
