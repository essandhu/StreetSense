"""Incident-provider Protocol and supporting value types — Phase 4.5.2.

The historical-correlation scorer (``scoring/historical/``) consumes
records persisted in the ``incidents`` PostGIS table (migration 0013).
This module is the contract the persistence path is filled from:
``IncidentProvider`` is the seam (extension point 3-style; see
``CLAUDE.md``); one concrete adapter ships in Phase 4 per ADR 0007.

Idempotency, incrementality, and streaming follow the same posture as
``ImageryProvider`` (ADR 0005): the natural key is
``(provider, provider_incident_id)`` for cross-run deduplication;
``within`` bounds the work to a date window for incremental pulls;
``fetch_for_bbox`` returns an Iterator so callers can persist page-by-
page rather than materializing the whole city's history in memory.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Protocol


class IncidentSeverity(StrEnum):
    """Severity classification for a reported incident.

    Three levels matching the canonical KABCO traffic-incident
    taxonomy collapsed into actionable categories for the
    historical-correlation scorer's recency-weighted KDE:

    - ``FATAL``: at least one fatality.
    - ``INJURY``: injury but no fatality (collapses A/B/C from KABCO).
    - ``PROPERTY_DAMAGE_ONLY``: no injuries, no fatalities (KABCO O).
    - ``UNKNOWN``: provider did not classify; treated as PDO for
      weighting.
    """

    FATAL = "fatal"
    INJURY = "injury"
    PROPERTY_DAMAGE_ONLY = "property_damage_only"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """WGS84 bounding box for spatial filtering.

    Phase 4 callers pass the city's bbox; future multi-region work
    can compose multiple bboxes per call.
    """

    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float


@dataclass(frozen=True, slots=True)
class IncidentRecord:
    """A single historical incident reference returned by a provider.

    Fields map directly to the ``incidents`` table (migration 0013).
    ``metadata`` is provider-specific and stored as JSONB so a future
    provider can ship richer fields (officer narrative, vehicle
    counts, contributing factors) without a schema change.
    """

    provider: str
    provider_incident_id: str
    lat: float
    lon: float
    incident_at: datetime
    severity: IncidentSeverity
    metadata: dict[str, Any] = field(default_factory=dict)


class IncidentProvider(Protocol):
    """Per-provider incident-data seam — extension point 3-style.

    See ``docs/adr/0007-incident-dataset.md`` for the MassDOT IMPACT
    choice and the conditions under which a second provider would
    slot in.

    Implementations MUST be:

    - **Idempotent.** The same call yields the same
      ``(provider, provider_incident_id)`` set across processes.
    - **Incremental.** ``within`` bounds the work to a date window; a
      ``None`` window means "consider any date".
    - **Streaming.** ``fetch_for_bbox`` returns an Iterator so
      callers can process pages as they arrive.

    Implementations MAY:

    - Cache locally for rate-limit politeness.
    - Raise provider-specific exceptions; callers do not catch
      anything other than the failure types declared on the
      protocol's docstrings.
    """

    name: str
    """Stable identifier for the provider (e.g., ``"massdot-impact"``).
    Used by the persistence layer to populate ``incidents.provider``
    and by tests to assert provider identity in responses."""

    def fetch_for_bbox(
        self,
        bbox: BoundingBox,
        *,
        within: tuple[date, date] | None = None,
    ) -> Iterator[IncidentRecord]:
        """Stream incident references inside ``bbox`` and ``within``.

        ``within`` is a closed ``(start, end)`` interval on
        ``incident_at.date()``; records outside this window MUST be
        skipped. A ``None`` window imposes no temporal filter.

        Yields one ``IncidentRecord`` per upstream match. A bbox with
        no matches yields nothing -- it does **not** raise. Callers
        detect "no incidents" by post-hoc count.
        """
        ...


__all__ = [
    "BoundingBox",
    "IncidentProvider",
    "IncidentRecord",
    "IncidentSeverity",
]
