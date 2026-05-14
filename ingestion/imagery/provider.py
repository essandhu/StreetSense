"""Imagery-provider protocol and supporting value types.

This module defines extension point 3 (see ``CLAUDE.md``). Provider
implementations live in sibling modules under ``ingestion/imagery/``.

The protocol is intentionally minimal: callers describe *where* they want
imagery (a list of waypoints), and providers return references and bytes.
Aggregation, deduplication, persistence, and MinIO upload live in
``ingestion/imagery/job.py`` so they do not need to be re-implemented per
provider.

Concrete shape (filled in by Task 3.2.1):

- ``fetch_for_waypoints(waypoints, *, within) -> Iterator[ImageryReference]``
- ``download_bytes(reference) -> bytes``

This file is intentionally minimal in Phase 3.1 (foundation). The full
protocol body lands with Task 3.2.1 alongside the Mapillary
implementation; the empty Protocol here lets ``mypy --strict`` pass on
the package today while signaling the seam exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Waypoint:
    """A single point along a segment at which imagery is requested.

    ``segment_id`` is the UUID of the parent ``road_segments`` row;
    ``sample_index`` is the zero-based position of the waypoint along the
    segment (so the same provider response can be consistently re-attached
    to the originating segment + position). ``lat``/``lon`` are in WGS84.
    """

    lat: float
    lon: float
    segment_id: UUID
    sample_index: int


@dataclass(frozen=True, slots=True)
class ImageryReference:
    """A pointer to a single piece of imagery returned by a provider.

    The ``provider`` + ``provider_image_id`` pair is the natural key the
    persistence layer uses to detect duplicates across runs. Camera
    parameters are provider-specific and stored as a free-form dict so a
    new provider can ship richer intrinsics without a schema migration.
    """

    provider: str
    provider_image_id: str
    segment_id: UUID
    sample_index: int
    capture_date: date
    heading_deg: float
    camera_params: dict[str, object]


class ImageryProvider(Protocol):
    """Per-provider imagery seam — extension point 3.

    See ``docs/adr/0005-imagery-provider.md`` for the Mapillary choice and
    the conditions under which a second provider would slot in here.

    Implementations are idempotent (the same ``waypoints`` + ``within``
    pair yields the same set of ``ImageryReference``s on re-run) and
    incremental (callers bound work by passing a ``within`` window).
    """

    name: str
    """Stable identifier for the provider (e.g., ``"mapillary"``). Used by
    the persistence layer to populate ``segment_imagery.provider``."""
