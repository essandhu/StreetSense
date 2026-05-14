"""Historical-correlation sub-score — Phase 4's fourth real risk factor.

Per-segment correlation between historical incident density (kernel-
density-estimated within a configurable radius) and segment proximity,
weighted by recency. Pure-functional once the incidents table is loaded;
time-invariant at the per-image scale (incident history is a static
input; recency-weighting is a parameter, not a per-call concern).

Sits behind the :class:`SubScorer` protocol from ``scoring.interface``
— the same seam the other three real sub-scores attach to — so the
scoring-run orchestration in ``scoring.run`` adds historical
correlation by configuration (extension point 1).

The incident data is provided by
:class:`ingestion.incidents.IncidentProvider` (extension point 3-style;
see ``docs/adr/0007-incident-dataset.md``).

Phase 4.1: empty scaffold; real implementation lands in Phase 4.5.13 —
see ``conductor/tracks/phase-4-propagator/plan.md`` Tasks 4.5.11–4.5.13.
"""

from __future__ import annotations

__all__: list[str] = []
