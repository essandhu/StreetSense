"""Imagery-provider protocol and supporting value types.

Extension point 3 (see ``CLAUDE.md`` and
``docs/adr/0005-imagery-provider.md``). Provider implementations live
in sibling modules under ``ingestion/imagery/``; callers see only this
``Protocol``.

The protocol is intentionally minimal. Aggregation, deduplication,
persistence, and MinIO upload live in ``ingestion/imagery/job.py`` so
they are not re-implemented per provider.

Idempotency: re-running ``fetch_for_waypoints`` with the same
``(waypoints, within)`` pair MUST yield the same
``provider_image_id`` set. Providers achieve this by deriving stable
IDs from the upstream API rather than minting them locally.

Incrementality: callers pass a ``within`` window to bound work. A
``None`` window means "any capture date".

Streaming: the protocol returns an ``Iterator`` rather than a list so
providers can page upstream without materializing the whole city's
imagery references in memory.
"""

from __future__ import annotations

from collections.abc import Iterator
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

    The ``(provider, provider_image_id)`` pair is the natural key the
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

    See ``docs/adr/0005-imagery-provider.md`` for the Mapillary choice
    and the conditions under which a second provider would slot in
    here.

    Implementations MUST be:

    - **Idempotent.** Same inputs yield the same reference set across
      calls and across processes.
    - **Incremental.** ``within`` bounds the work; a ``None`` window
      means "consider any capture date".
    - **Streaming.** ``fetch_for_waypoints`` returns an ``Iterator`` so
      callers can process pages as they arrive.

    Implementations MAY:

    - Cache locally (e.g., a token-bucket rate-limiter for API
      politeness). They MUST NOT cache results in a way that breaks
      idempotency across processes.
    - Raise provider-specific exceptions; callers do not catch
      anything other than the failure types declared on the protocol's
      docstrings (none in Phase 3).
    """

    name: str
    """Stable identifier for the provider (e.g., ``"mapillary"``). Used
    by the persistence layer to populate ``segment_imagery.provider``
    and by tests to assert provider identity in responses."""

    def fetch_for_waypoints(
        self,
        waypoints: list[Waypoint],
        *,
        within: tuple[date, date] | None = None,
    ) -> Iterator[ImageryReference]:
        """Stream imagery references for each waypoint in ``waypoints``.

        ``within`` is a closed ``(start, end)`` interval on
        ``capture_date``; references outside this window MUST be
        skipped. A ``None`` window imposes no temporal filter.

        Yields one ``ImageryReference`` per upstream match. A waypoint
        with no matches yields nothing for that waypoint — it does
        **not** raise. Callers detect "no imagery" by post-hoc count.
        """
        ...

    def download_bytes(self, reference: ImageryReference) -> bytes:
        """Return the raw image bytes for ``reference``.

        Callers handle persistence (MinIO upload, row write). This
        method MAY hit the network on every call; providers SHOULD
        respect their published rate limits via internal pacing.
        """
        ...
