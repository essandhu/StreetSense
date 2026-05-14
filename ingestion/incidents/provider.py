"""Incident-provider protocol and supporting value types — Phase 4.5.2 placeholder.

The ``IncidentProvider(Protocol)`` (extension point 3-style; see
``CLAUDE.md`` and ``docs/adr/0007-incident-dataset.md``) lands in
Task 4.5.2. Provider implementations will live in sibling modules under
``ingestion/incidents/`` and callers will see only this Protocol —
mirroring the Phase 3 ``ImageryProvider`` shape.

Phase 4.1: empty scaffold. See ``__init__.py`` for the high-level design
and ``conductor/tracks/phase-4-propagator/plan.md`` for the task
breakdown.
"""

from __future__ import annotations
