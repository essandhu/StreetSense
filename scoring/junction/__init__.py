"""Junction-complexity sub-score — Phase 4's third real risk factor.

Per-segment complexity score derived from OSM topology alone (no imagery,
no external data). The score combines intersection degree at the
segment's endpoints, merge-angle sharpness, lane-count changes, and
road-class transitions into a [0, 1] value. Pure-functional and
deterministic; time-invariant at the per-image scale (topology does not
change hourly).

Sits behind the :class:`SubScorer` protocol from ``scoring.interface``
— the same seam Phase 2's glare and Phase 3's perception scorers attach
to — so the scoring-run orchestration in ``scoring.run`` adds junction
complexity by configuration (extension point 1).

Phase 4.1: empty scaffold; real implementation lands in Phase 4.5 — see
``conductor/tracks/phase-4-propagator/plan.md`` Tasks 4.5.8-4.5.10.
"""

from __future__ import annotations

__all__: list[str] = []
