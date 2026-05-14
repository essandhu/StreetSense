"""Historical incident ingestion — Phase 4's incident-dataset adapter package.

Extension point 3-style: ``IncidentProvider(Protocol)`` lives here
(``ingestion.incidents.provider``); Phase 4 ships **one** concrete
adapter for the dataset chosen by ADR 0007; a second adapter drops in
later as a sibling module without caller changes.

Sister to ``ingestion.imagery`` (Phase 3's analogous extension point;
see ADR 0005), with the same posture: protocol + concrete adapter +
job module + vcrpy-recorded integration tests.

Phase 4.1: empty scaffold; the protocol lands in Task 4.5.2, the
concrete adapter in 4.5.5, and the ingestion job in 4.5.6 — see
``conductor/tracks/phase-4-propagator/plan.md`` Tasks 4.5.1-4.5.7.
"""

from __future__ import annotations

from .provider import BoundingBox, IncidentProvider, IncidentRecord, IncidentSeverity

__all__ = [
    "BoundingBox",
    "IncidentProvider",
    "IncidentRecord",
    "IncidentSeverity",
]
